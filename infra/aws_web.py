"""Provisiona o site em uma VPC separada, sem SSH ou acesso de rede aos PCs."""

import json
import time
from pathlib import Path

import boto3

STATE = Path('runtime/aws-web.json')
NAME = 'fiap-switch-web-20261027'


def provision():
    lab = json.loads(Path('runtime/aws-validation-20261027.json').read_text())
    session = boto3.Session(profile_name='fiap-switch', region_name=lab['region'])
    ec2, iam, ssm = (session.client(service) for service in ('ec2', 'iam', 'ssm'))
    state = json.loads(STATE.read_text()) if STATE.exists() else {'region': lab['region']}
    def save():
        STATE.write_text(json.dumps(state, indent=2))
    trust = {'Version': '2012-10-17', 'Statement': [{'Effect': 'Allow', 'Principal': {'Service': 'ec2.amazonaws.com'}, 'Action': 'sts:AssumeRole'}]}
    try:
        iam.get_role(RoleName=NAME)
    except iam.exceptions.NoSuchEntityException:
        iam.create_role(RoleName=NAME, AssumeRolePolicyDocument=json.dumps(trust), Tags=[{'Key': 'Project', 'Value': NAME}])
    iam.attach_role_policy(RoleName=NAME, PolicyArn='arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore')
    account = session.client('sts').get_caller_identity()['Account']
    policy = {'Version': '2012-10-17', 'Statement': [
        {'Effect': 'Allow', 'Action': ['ec2:DescribeInstances', 'ec2:DescribeInstanceTypes', 'ec2:DescribeVolumes'], 'Resource': '*', 'Condition': {'StringEquals': {'aws:RequestedRegion': lab['region']}}},
        {'Effect': 'Allow', 'Action': ['ec2:StartInstances', 'ec2:StopInstances', 'ec2:RebootInstances'],
         'Resource': [f"arn:aws:ec2:{lab['region']}:{account}:instance/{target['instance_id']}" for target in lab['targets']],
         'Condition': {'StringEquals': {'ec2:ResourceTag/FIAPSwitchManaged': 'true'}}},
    ]}
    iam.put_role_policy(RoleName=NAME, PolicyName='EnergiaEInventarioDosAlvos', PolicyDocument=json.dumps(policy))
    try:
        profile = iam.get_instance_profile(InstanceProfileName=NAME)['InstanceProfile']
    except iam.exceptions.NoSuchEntityException:
        profile = iam.create_instance_profile(InstanceProfileName=NAME)['InstanceProfile']
    if not profile['Roles']:
        iam.add_role_to_instance_profile(InstanceProfileName=NAME, RoleName=NAME)
    state['role'] = NAME
    if 'vpc' not in state:
        state['vpc'] = ec2.create_vpc(CidrBlock='10.50.0.0/16', TagSpecifications=[{'ResourceType': 'vpc', 'Tags': [{'Key': 'Project', 'Value': NAME}]}])['Vpc']['VpcId']; save()
        ec2.modify_vpc_attribute(VpcId=state['vpc'], EnableDnsSupport={'Value': True})
        ec2.modify_vpc_attribute(VpcId=state['vpc'], EnableDnsHostnames={'Value': True})
    if 'subnet' not in state:
        state['subnet'] = ec2.create_subnet(VpcId=state['vpc'], CidrBlock='10.50.1.0/24')['Subnet']['SubnetId']; save()
    if 'gateway' not in state:
        state['gateway'] = ec2.create_internet_gateway()['InternetGateway']['InternetGatewayId']; save()
        ec2.attach_internet_gateway(InternetGatewayId=state['gateway'], VpcId=state['vpc'])
    if 'route_table' not in state:
        state['route_table'] = ec2.create_route_table(VpcId=state['vpc'])['RouteTable']['RouteTableId']; save()
        ec2.create_route(RouteTableId=state['route_table'], DestinationCidrBlock='0.0.0.0/0', GatewayId=state['gateway'])
        ec2.associate_route_table(RouteTableId=state['route_table'], SubnetId=state['subnet'])
    if 'group' not in state:
        state['group'] = ec2.create_security_group(GroupName=NAME, Description='HTTPS do painel; sem SSH e sem rede dos laboratorios', VpcId=state['vpc'])['GroupId']; save()
        ec2.authorize_security_group_ingress(GroupId=state['group'], IpPermissions=[{'IpProtocol': 'tcp', 'FromPort': port, 'ToPort': port, 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]} for port in (80, 443)])
    if 'instance' not in state:
        ami = ssm.get_parameter(Name='/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64')['Parameter']['Value']
        user_data = '''#!/bin/bash
set -euo pipefail
dnf install -y docker git
systemctl enable --now docker
mkdir -p /usr/local/lib/docker/cli-plugins /opt/fiap-switch
curl --fail --location --silent --show-error https://github.com/docker/compose/releases/download/v5.6.1/docker-compose-linux-x86_64 -o /tmp/docker-compose
echo '47737f4236e6d3ccea7291995203f8f018058783b4c3a9469119811a0b5db8c0  /tmp/docker-compose' | sha256sum --check -
install -m 0755 /tmp/docker-compose /usr/local/lib/docker/cli-plugins/docker-compose
curl --fail --location --silent --show-error https://github.com/docker/buildx/releases/download/v0.38.1/buildx-v0.38.1.linux-amd64 -o /tmp/docker-buildx
echo 'e25d5cd675092bb5d1d03632fdc46cdf3fede7b514dc5f023a105e00d3ce5ad4  /tmp/docker-buildx' | sha256sum --check -
install -m 0755 /tmp/docker-buildx /usr/local/lib/docker/cli-plugins/docker-buildx
'''
        for attempt in range(12):
            try:
                node = ec2.run_instances(ImageId=ami, InstanceType='t3.small', MinCount=1, MaxCount=1, ClientToken=NAME,
                    IamInstanceProfile={'Name': NAME}, UserData=user_data, MetadataOptions={'HttpTokens': 'required', 'HttpEndpoint': 'enabled', 'HttpPutResponseHopLimit': 2},
                    NetworkInterfaces=[{'DeviceIndex': 0, 'SubnetId': state['subnet'], 'Groups': [state['group']], 'AssociatePublicIpAddress': True}],
                    BlockDeviceMappings=[{'DeviceName': '/dev/xvda', 'Ebs': {'VolumeSize': 20, 'VolumeType': 'gp3', 'Encrypted': True, 'DeleteOnTermination': True}}],
                    TagSpecifications=[{'ResourceType': 'instance', 'Tags': [{'Key': 'Project', 'Value': NAME}, {'Key': 'Name', 'Value': NAME}]}])['Instances'][0]
                state['instance'] = node['InstanceId']; save(); break
            except ec2.exceptions.ClientError as error:
                if attempt == 11 or 'instance profile' not in str(error).lower():
                    raise
                time.sleep(5)
    if 'allocation' not in state:
        address = ec2.allocate_address(Domain='vpc', TagSpecifications=[{'ResourceType': 'elastic-ip', 'Tags': [{'Key': 'Project', 'Value': NAME}]}])
        state.update(allocation=address['AllocationId'], ip=address['PublicIp']); save()
    ec2.get_waiter('instance_running').wait(InstanceIds=[state['instance']], WaiterConfig={'Delay': 5, 'MaxAttempts': 60})
    ec2.associate_address(InstanceId=state['instance'], AllocationId=state['allocation'])
    if 'data_volume' not in state:
        node = ec2.describe_instances(InstanceIds=[state['instance']])['Reservations'][0]['Instances'][0]
        state['data_volume'] = ec2.create_volume(AvailabilityZone=node['Placement']['AvailabilityZone'], Size=8, VolumeType='gp3', Encrypted=True,
            TagSpecifications=[{'ResourceType':'volume','Tags':[{'Key':'Project','Value':NAME},{'Key':'Name','Value':'fiap-switch-dados-persistentes'}]}])['VolumeId']; save()
    volume = ec2.describe_volumes(VolumeIds=[state['data_volume']])['Volumes'][0]
    if not volume['Attachments']:
        ec2.get_waiter('volume_available').wait(VolumeIds=[state['data_volume']], WaiterConfig={'Delay':2,'MaxAttempts':30})
        ec2.attach_volume(VolumeId=state['data_volume'], InstanceId=state['instance'], Device='/dev/sdf')
    ec2.modify_instance_attribute(InstanceId=state['instance'], BlockDeviceMappings=[{'DeviceName':'/dev/sdf','Ebs':{'DeleteOnTermination':False}}])
    state['url'] = f"https://fiap-switch.{state['ip']}.sslip.io"
    ec2.create_tags(Resources=[target['instance_id'] for target in lab['targets']], Tags=[{'Key': 'FIAPSwitchManaged', 'Value': 'true'}])
    save()
    print('Servidor web criado em VPC separada; IPv4 estável e entrada somente em 80/443.')


if __name__ == '__main__':
    provision()
