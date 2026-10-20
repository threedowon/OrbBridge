"""Bounded important-event totals, independent of the scrolling log buffer."""
import re
import threading
import time


class ImportantIssues:
    def __init__(self):
        self.lock = threading.Lock()
        self.rows = {}
        self.bad = None

    def record(self, line, source, metadata):
        with self.lock:
            crc = re.fullmatch(r"#master alive relay=\d+ bad=(\d+)", line)
            count = 1
            if crc:
                bad = int(crc[1])
                count = bad if self.bad is None or bad < self.bad else bad - self.bad
                self.bad = bad
                if not count:
                    return
                key, title, severity = 'crc', 'RS485 CRC 오류', 'warn'
            elif '[recover]' in line and 'added 유실' in line:
                slot = re.search(r'slave=(\d+) cell=(\d+)', line)
                location = f'S{slot[1]} C{slot[2]}' if slot else ''
                key, title, severity = 'added:' + location, '올림 유실 추정 · ' + location, 'error'
            elif '[recover]' in line and 'removed 유실' in line:
                key, title, severity = 'removed', '제거 유실 복구 완료', 'warn'
            elif '통신 끊김' in line:
                key, title, severity = 'connection', '통신 끊김 발생', 'error'
            elif '게임 입력 처리 실패' in line:
                key, title, severity = 'input', '게임 입력 처리 실패', 'error'
            elif metadata['category'] == 'MIDI' and (metadata['level'] in ('ERROR', 'WARN') or 'skip' in line.lower()):
                key, title, severity = 'midi', 'MIDI 출력 이상', 'error'
            elif metadata['level'] == 'ERROR':
                key = 'error:' + metadata['category']
                title, severity = metadata['category'] + ' 오류', 'error'
            else:
                return
            previous = self.rows.get(key, {})
            self.rows[key] = dict(key=key, title=title, severity=severity,
                                  count=previous.get('count', 0) + count,
                                  last=time.time() * 1000, detail=line[:500])

    def snapshot(self):
        with self.lock:
            return [dict(row) for row in self.rows.values()]

    def clear(self):
        with self.lock:
            self.rows.clear()  # Keep the CRC baseline: unchanged totals aren't new errors.


important_issues = ImportantIssues()
