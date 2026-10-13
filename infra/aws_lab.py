"""Provision an isolated WinRM test lab. State and credentials stay in runtime/."""
import argparse
import json
import secrets
import time
from pathlib import Path

import boto3

STATE = Path('runtime/aws-lab.json')
NAME = 'fiap-switch-e2e'


def clients():
    session = boto3.Session(profile_name='fiap-switch', region_name='sa-east-1')
    return session.client('ec2'), session.client('iam'), session.client('ssm')


def save(state):
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2))


def provision():
    if STATE.exists():
        raise SystemExit('Existing lab state found. Inspect or destroy that lab before provisioning.')
    ec2, iam, ssm = clients()
    state = {'region': 'sa-east-1', 'instances': [], 'created': time.time()}
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
    group = ec2.create_security_group(GroupName=NAME, Description='Private WinRM lab; no public inbound access', VpcId=vpc)['GroupId']
    state['group'] = group
    save(state)
    ec2.authorize_security_group_ingress(GroupId=group, IpPermissions=[{'IpProtocol': 'tcp', 'FromPort': 5986, 'ToPort': 5986, 'UserIdGroupPairs': [{'GroupId': group}]}])
    password = secrets.token_urlsafe(30) + 'aA1!'
    state['winrm_user'] = 'LabOperator'
    state['winrm_password'] = password
    save(state)
    windows_ami = ssm.get_parameter(Name='/aws/service/ami-windows-latest/Windows_Server-2022-English-Full-Base')['Parameter']['Value']
    linux_ami = ssm.get_parameter(Name='/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64')['Parameter']['Value']
    windows_script = '<powershell>' + (Path(__file__).parent/'windows-bootstrap.ps1').read_text().replace('__PASSWORD__', password).replace('10.3.31.1', '10.3.31.11') + '</powershell>'
    linux_script = '#!/bin/bash\nset -eu\ndnf install -y python3 python3-pip\nmkdir -p /opt/fiap-switch\n'
    # IAM propagation is eventually consistent. Retry only the pre-instance validation.
    for kind, ami, instance_type, ip, script in [
        ('controller', linux_ami, 't3.micro', '10.3.31.10', linux_script),
        ('windows', windows_ami, 't3.small', '10.3.31.11', windows_script),
    ]:
        for attempt in range(12):
            try:
                launched = ec2.run_instances(ImageId=ami, InstanceType=instance_type, MinCount=1, MaxCount=1,
                    IamInstanceProfile={'Name': NAME}, UserData=script,
                    NetworkInterfaces=[{'DeviceIndex': 0, 'SubnetId': subnet, 'Groups': [group], 'PrivateIpAddress': ip, 'AssociatePublicIpAddress': True}],
                    MetadataOptions={'HttpTokens': 'required', 'HttpEndpoint': 'enabled'},
                    BlockDeviceMappings=[{'DeviceName': '/dev/sda1' if kind == 'windows' else '/dev/xvda', 'Ebs': {'VolumeSize': 30 if kind == 'windows' else 8, 'VolumeType': 'gp3', 'Encrypted': True, 'DeleteOnTermination': True}}],
                    TagSpecifications=[{'ResourceType': 'instance', 'Tags': [{'Key': 'Name', 'Value': f'{NAME}-{kind}'}, {'Key': 'Project', 'Value': NAME}]}])['Instances'][0]['InstanceId']
                state[kind] = launched
                state['instances'].append(launched)
                save(state)
                print(f'{kind}: {launched}', flush=True)
                break
            except ec2.exceptions.ClientError as error:
                if 'instance profile' not in str(error).lower() or attempt == 11:
                    raise
                time.sleep(5)
    print('Provisioned. Use status, send, stop, or destroy.', flush=True)


def command(ssm, instance, script, windows=False, timeout=180):
    response = ssm.send_command(InstanceIds=[instance], DocumentName='AWS-RunPowerShellScript' if windows else 'AWS-RunShellScript',
        Parameters={'commands': [script], 'executionTimeout': [str(timeout)]}, TimeoutSeconds=timeout)
    command_id = response['Command']['CommandId']
    print(f'Command: {command_id}', flush=True)
    return command_id


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['provision', 'status', 'stop', 'destroy', 'send'])
    parser.add_argument('--target', choices=['controller', 'windows'], default='controller')
    parser.add_argument('--script')
    args = parser.parse_args()
    if args.action == 'provision':
        provision()
        return
    state = json.loads(STATE.read_text())
    ec2, iam, ssm = clients()
    if args.action == 'status':
        for reservation in ec2.describe_instances(InstanceIds=state['instances'])['Reservations']:
            for instance in reservation['Instances']:
                print(instance['InstanceId'], instance['State']['Name'])
        print('SSM:', [(i['InstanceId'], i['PingStatus']) for i in ssm.describe_instance_information()['InstanceInformationList'] if i['InstanceId'] in state['instances']])
    elif args.action == 'stop':
        ec2.stop_instances(InstanceIds=state['instances'])
        print('Stop requested for all lab instances.')
    elif args.action == 'send':
        command(ssm, state[args.target], Path(args.script).read_text(), args.target == 'windows')
    elif args.action == 'destroy':
        if state['instances']:
            ec2.terminate_instances(InstanceIds=state['instances'])
            ec2.get_waiter('instance_terminated').wait(InstanceIds=state['instances'])
        if state.get('group'):
            ec2.delete_security_group(GroupId=state['group'])
        if state.get('subnet'):
            ec2.delete_subnet(SubnetId=state['subnet'])
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
        print('All owned lab resources destroyed.')


if __name__ == '__main__':
    main()
