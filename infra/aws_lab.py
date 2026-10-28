"""Cria um laboratório WinRM isolado. Estado e credenciais ficam em runtime/."""
import argparse
import json
import secrets
import time
from pathlib import Path

import boto3

STATE = Path('runtime/aws-lab.json')
NAME = 'fiap-switch-e2e'
REGION = 'sa-east-1'


def clients(region=None):
    session = boto3.Session(profile_name='fiap-switch', region_name=region or REGION)
    return session.client('ec2'), session.client('iam'), session.client('ssm')


def save(state):
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2))


def provision(windows_controller=False, target_count=1):
    if STATE.exists():
        raise SystemExit('Já existe um laboratório registrado. Consulte ou remova esse ambiente antes de criar outro.')
    ec2, iam, ssm = clients()
    state = {'region': REGION, 'instances': [], 'created': time.time(), 'controller_platform': 'windows' if windows_controller else 'linux', 'targets': []}
    save(state)
    trust = {'Version': '2012-10-17', 'Statement': [{'Effect': 'Allow', 'Principal': {'Service': 'ec2.amazonaws.com'}, 'Action': 'sts:AssumeRole'}]}
    role = iam.create_role(RoleName=NAME, AssumeRolePolicyDocument=json.dumps(trust), Tags=[{'Key': 'Project', 'Value': NAME}])['Role']['RoleName']
    state['role'] = role
    save(state)
    iam.attach_role_policy(RoleName=role, PolicyArn='arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore')
    iam.create_instance_profile(InstanceProfileName=NAME)
    state['profile'] = NAME
    save(state)
    iam.add_role_to_instance_profile(InstanceProfileName=NAME, RoleName=role)
    vpc = ec2.create_vpc(CidrBlock='10.3.0.0/16', TagSpecifications=[{'ResourceType': 'vpc', 'Tags': [{'Key': 'Name', 'Value': NAME}]}])['Vpc']['VpcId']
    state['vpc'] = vpc
    save(state)
    ec2.modify_vpc_attribute(VpcId=vpc, EnableDnsSupport={'Value': True})
    ec2.modify_vpc_attribute(VpcId=vpc, EnableDnsHostnames={'Value': True})
    subnet = ec2.create_subnet(VpcId=vpc, CidrBlock='10.3.31.0/24')['Subnet']['SubnetId']
    state['subnet'] = subnet
    state['subnets'] = [subnet]
    save(state)
    gateway = ec2.create_internet_gateway()['InternetGateway']['InternetGatewayId']
    state['gateway'] = gateway
    save(state)
    ec2.attach_internet_gateway(InternetGatewayId=gateway, VpcId=vpc)
    table = ec2.create_route_table(VpcId=vpc)['RouteTable']['RouteTableId']
    state['route_table'] = table
    save(state)
    ec2.create_route(RouteTableId=table, DestinationCidrBlock='0.0.0.0/0', GatewayId=gateway)
    ec2.associate_route_table(RouteTableId=table, SubnetId=subnet)
    group = ec2.create_security_group(GroupName=NAME, Description='Laboratório WinRM privado, sem portas públicas de entrada', VpcId=vpc)['GroupId']
    state['group'] = group
    save(state)
    ec2.authorize_security_group_ingress(GroupId=group, IpPermissions=[{'IpProtocol': 'tcp', 'FromPort': 5986, 'ToPort': 5986, 'UserIdGroupPairs': [{'GroupId': group}]}])
    password = secrets.token_urlsafe(30) + 'aA1!'
    state['winrm_user'] = 'LabOperator'
    state['winrm_password'] = password
    save(state)
    windows_ami = ssm.get_parameter(Name='/aws/service/ami-windows-latest/Windows_Server-2022-English-Full-Base')['Parameter']['Value']
    linux_ami = ssm.get_parameter(Name='/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64')['Parameter']['Value']
    def windows_script(ip):
        script = (Path(__file__).parent/'windows-bootstrap.ps1').read_text()
        return '<powershell>' + script.replace('__PASSWORD__', password).replace('10.3.31.1', ip).replace('10.3.31.0/24', '10.3.0.0/16') + '</powershell>'
    linux_script = '#!/bin/bash\nset -eu\ndnf install -y python3.12 python3.12-pip\nmkdir -p /opt/fiap-switch\n'
    nodes = [('controller', windows_ami if windows_controller else linux_ami, 't3.small' if windows_controller else 't3.micro', '10.3.31.10',
              '<powershell>New-Item -ItemType Directory -Path C:\\fiap-switch -Force</powershell>' if windows_controller else linux_script, subnet)]
    for index in range(target_count):
        ip = ('10.3.31.4' if windows_controller else '10.3.31.11') if index == 0 else '10.3.22.100'
        target_subnet = subnet
        if index == 1:
            target_subnet = ec2.create_subnet(VpcId=vpc, CidrBlock='10.3.22.0/24')['Subnet']['SubnetId']
            ec2.associate_route_table(RouteTableId=table, SubnetId=target_subnet)
            state['subnets'].append(target_subnet)
            save(state)
        nodes.append(('windows' if index == 0 else 'windows2', windows_ami, 't3.small', ip, windows_script(ip), target_subnet))
    # A propagação do IAM é gradual. Repita somente a validação anterior à criação da instância.
    for kind, ami, instance_type, ip, script, node_subnet in nodes:
        for attempt in range(12):
            try:
                launched = ec2.run_instances(ImageId=ami, InstanceType=instance_type, MinCount=1, MaxCount=1,
                    IamInstanceProfile={'Name': NAME}, UserData=script,
                    NetworkInterfaces=[{'DeviceIndex': 0, 'SubnetId': node_subnet, 'Groups': [group], 'PrivateIpAddress': ip, 'AssociatePublicIpAddress': True}],
                    MetadataOptions={'HttpTokens': 'required', 'HttpEndpoint': 'enabled'},
                    BlockDeviceMappings=[{'DeviceName': '/dev/sda1' if ami == windows_ami else '/dev/xvda', 'Ebs': {'VolumeSize': 30 if ami == windows_ami else 8, 'VolumeType': 'gp3', 'Encrypted': True, 'DeleteOnTermination': True}}],
                    TagSpecifications=[{'ResourceType': 'instance', 'Tags': [{'Key': 'Name', 'Value': f'{NAME}-{kind}'}, {'Key': 'Project', 'Value': NAME}]}])['Instances'][0]['InstanceId']
                state[kind] = launched
                state['instances'].append(launched)
                if kind.startswith('windows'):
                    state['targets'].append({'instance_id': launched, 'ip': ip, 'computer_id': f'301-{ip.split(".")[-1]}' if kind == 'windows' else '202-100'})
                save(state)
                print(f'{kind}: {launched}', flush=True)
                break
            except ec2.exceptions.ClientError as error:
                if 'instance profile' not in str(error).lower() or attempt == 11:
                    raise
                time.sleep(5)
    print('Laboratório criado. Use status, send, stop ou destroy.', flush=True)


def command(ssm, instance, script, windows=False, timeout=180):
    response = ssm.send_command(InstanceIds=[instance], DocumentName='AWS-RunPowerShellScript' if windows else 'AWS-RunShellScript',
        Parameters={'commands': [script], 'executionTimeout': [str(timeout)]}, TimeoutSeconds=timeout)
    command_id = response['Command']['CommandId']
    print(f'Comando: {command_id}', flush=True)
    return command_id


def main():
    global STATE, REGION, NAME
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['provision', 'status', 'stop', 'destroy', 'send'])
    parser.add_argument('--target', choices=['controller', 'windows', 'windows2'], default='controller')
    parser.add_argument('--script')
    parser.add_argument('--region', default=REGION)
    parser.add_argument('--state', type=Path, default=STATE)
    parser.add_argument('--name', default=NAME)
    parser.add_argument('--windows-controller', action='store_true')
    parser.add_argument('--targets', type=int, choices=[1, 2], default=1)
    args = parser.parse_args()
    STATE, REGION, NAME = args.state, args.region, args.name
    if args.action == 'provision':
        provision(args.windows_controller, args.targets)
        return
    state = json.loads(STATE.read_text())
    REGION = state['region']
    ec2, iam, ssm = clients()
    if args.action == 'status':
        for reservation in ec2.describe_instances(InstanceIds=state['instances'])['Reservations']:
            for instance in reservation['Instances']:
                print(instance['InstanceId'], instance['State']['Name'])
        print('SSM:', [(i['InstanceId'], i['PingStatus']) for i in ssm.describe_instance_information()['InstanceInformationList'] if i['InstanceId'] in state['instances']])
    elif args.action == 'stop':
        ec2.stop_instances(InstanceIds=state['instances'])
        print('Desligamento solicitado para todas as instâncias do laboratório.')
    elif args.action == 'send':
        command(ssm, state[args.target], Path(args.script).read_text(), args.target.startswith('windows') or state.get('controller_platform')=='windows')
    elif args.action == 'destroy':
        if state['instances']:
            ec2.terminate_instances(InstanceIds=state['instances'])
            ec2.get_waiter('instance_terminated').wait(InstanceIds=state['instances'])
        if state.get('group'):
            ec2.delete_security_group(GroupId=state['group'])
        for subnet in state.get('subnets', [state['subnet']] if state.get('subnet') else []):
            ec2.delete_subnet(SubnetId=subnet)
        if state.get('route_table'):
            ec2.delete_route_table(RouteTableId=state['route_table'])
        if state.get('gateway'):
            ec2.detach_internet_gateway(InternetGatewayId=state['gateway'], VpcId=state['vpc'])
            ec2.delete_internet_gateway(InternetGatewayId=state['gateway'])
        if state.get('vpc'):
            ec2.delete_vpc(VpcId=state['vpc'])
        if state.get('profile'):
            iam.remove_role_from_instance_profile(InstanceProfileName=state['profile'], RoleName=state['role'])
            iam.delete_instance_profile(InstanceProfileName=state['profile'])
        if state.get('role'):
            iam.detach_role_policy(RoleName=state['role'], PolicyArn='arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore')
            iam.delete_role(RoleName=state['role'])
        state['destroyed'] = time.time()
        save(state)
        print('Todos os recursos deste laboratório foram removidos.')


if __name__ == '__main__':
    main()
