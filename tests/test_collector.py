import ipaddress
from unittest.mock import MagicMock, patch

import pytest

from collector.service import Controller


@pytest.fixture
def controller(monkeypatch):
    for key, value in {'SWITCH_URL': 'https://switch.example.com', 'COLLECTOR_TOKEN': 'test-token', 'WINRM_USERNAME': 'FIAP\\svc-switch', 'WINRM_PASSWORD': 'test-password'}.items():
        monkeypatch.setenv(key, value)
    return Controller()


def test_wake_packet_and_lan_boundary(controller):
    pc = {'ip': '10.3.31.1', 'hostname': '', 'mac': '00:11:22:33:44:55', 'broadcast': '10.3.31.255'}
    with patch('collector.service.socket.socket') as factory:
        sock = factory.return_value.__enter__.return_value
        controller.wake(pc)
        packet, target = sock.sendto.call_args.args
        assert packet == b'\xff'*6 + bytes.fromhex('001122334455')*16
        assert target == ('10.3.31.255', 9)
        assert sock.sendto.call_count == 3
    with pytest.raises(ValueError):
        controller.target({**pc, 'ip': '127.0.0.1'})
    with pytest.raises(ValueError):
        controller.wake({**pc, 'broadcast': '8.8.8.8'})


def test_power_actions_are_fixed_and_encrypted(controller):
    with patch('collector.service.winrm.Session') as session:
        session.return_value.run_ps.return_value.status_code = 0
        controller.execute({'action': 'shutdown', 'ip': '10.3.31.1', 'hostname': ''})
        assert '/t 60' in session.return_value.run_ps.call_args.args[0]
        assert session.call_args.kwargs['message_encryption'] == 'always'
        assert session.call_args.kwargs['server_cert_validation'] == 'validate'
    with pytest.raises(ValueError):
        controller.execute({'action': 'custom-script', 'ip': '10.3.31.1'})


def test_winrm_failure_does_not_claim_power_off(controller):
    with patch('collector.service.socket.create_connection'), patch.object(controller, 'session', side_effect=TimeoutError('sensitive details')):
        report = controller.probe({'id': '301-1', 'ip': '10.3.31.1', 'hostname': ''})
        assert report['status'] == 'unreachable'
        assert 'sensitive details' not in report['issue']


def test_offline_probe_uses_short_tcp_timeout_and_never_connects_outside_lan(controller):
    with patch('collector.service.socket.create_connection',side_effect=TimeoutError) as tcp, patch.object(controller,'session') as winrm:
        result=controller.probe({'id':'301-1','ip':'10.3.31.1','hostname':''})
        assert result['status']=='unreachable' and not winrm.called
        assert tcp.call_args.kwargs['timeout']==2
        tcp.reset_mock()
        result=controller.probe({'id':'outside','ip':'8.8.8.8','hostname':''})
        assert result['status']=='unreachable' and not tcp.called
