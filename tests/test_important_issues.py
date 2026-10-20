from server.important_issues import ImportantIssues
from server.text_log import describe_log


def record(issues, line, source='STDOUT'):
    issues.record(line, source, describe_log(line, source))


def test_crc_counts_deltas_not_heartbeat_messages_and_survives_reset():
    issues = ImportantIssues()
    for bad in (0, 3, 3, 12, 12):
        record(issues, f'#master alive relay=100 bad={bad}', 'SERIAL/heartbeat')
    assert len(issues.snapshot()) == 1
    assert issues.snapshot()[0]['count'] == 12
    issues.clear()
    record(issues, '#master alive relay=100 bad=12')
    assert issues.snapshot() == []
    record(issues, '#master alive relay=110 bad=14')
    assert issues.snapshot()[0]['count'] == 2
    record(issues, '#master alive relay=1 bad=1')
    assert issues.snapshot()[0]['count'] == 3


def test_recovery_grouping_and_normal_log_volume():
    issues = ImportantIssues()
    for _ in range(3):
        record(issues, '[recover] slave=2 cell=4 note=C → added 유실 추정(미상, admin만)')
    for cell in (4, 5):
        record(issues, f'[recover] slave=2 cell={cell} → removed 유실 복구(screen+admin)')
    for _ in range(1000):
        record(issues, '[added] normal orb input')
    rows = {r['key']: r for r in issues.snapshot()}
    assert len(rows) == 2
    assert rows['added:S2 C4']['count'] == 3
    assert rows['removed']['count'] == 2


def test_input_and_midi_errors_are_kept_without_error_flood():
    issues = ImportantIssues()
    record(issues, '게임 입력 처리 실패: boom', 'SERIAL/error')
    record(issues, '[midi] skip no output')
    for _ in range(20):
        record(issues, '통신 끊김 | Slave 2', 'SLAVE')
    assert {r['key']:r['count'] for r in issues.snapshot()} == {'input':1, 'midi':1, 'connection':20}
