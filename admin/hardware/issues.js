/* Important history is server-owned; live faults are retained for this page session. */
class IssuePanel {
  constructor(element) {
    this.element = element;
    this.live = new Map();
    try { this.live = new Map(JSON.parse(sessionStorage.getItem('orb-important-live') || '[]')); } catch {}
    this.records = [];
    this.pendingSlots = new Set();
  }
  condition(key, active, title, detail = '') {
    let row = this.live.get(key);
    if (active) {
      if (!row) row = {key, title, count: 0, severity: 'error'};
      if (!row.active) { row.count++; row.last = Date.now(); }
      Object.assign(row, {active: true, detail});
      this.live.set(key, row);
    } else if (row) row.active = false;
    this.render();
  }
  status(s) {
    this.condition('status', false, '서버 상태 조회 실패');
    const hardware = s.hardware || {};
    const enabled = s.input_mode !== 'table';
    this.condition('port', enabled && !s.serial?.connected, 'COM 포트 연결 안 됨', s.serial?.error || s.serial?.port || '장치 연결을 확인하세요');
    const current = new Set();
    for (const c of hardware.connections || []) {
      const key = 'port:' + c.port; current.add(key);
      this.condition(key, enabled && (c.health === 'unresponsive' || Boolean(c.error)), c.port + ' 연결 / 응답 이상', c.error || '하트비트 없음');
    }
    for (const sv of s.slaves || []) {
      const key = 'slave:' + sv.slave; current.add(key);
      const expected = sv.has_heartbeat || (hardware.mode === 'master' && (hardware.connections || []).some(c => c.health === 'alive'));
      this.condition(key, enabled && expected && !sv.online, 'Slave ' + sv.slave + ' 하트비트 없음', '해당 슬롯의 입력 상태를 확인하세요');
    }
    for (const key of this.live.keys())
      if ((key.startsWith('port:') || key.startsWith('slave:')) && !current.has(key)) this.condition(key, false);
    const matrix = s.matrix;
    if (matrix) this.condition('matrix', matrix.state === 'disconnected', '매트릭스 연결 끊김', matrix.error || matrix.port || '');
    if (s.game?.cells) {
      this.pendingSlots = new Set(Object.entries(s.game.cells).filter(([,c]) => String(c.orb_id || '').startsWith('__recovered__:')).map(([key]) => key));
    }
    this.render();
  }
  history(rows) { this.records = rows; this.render(); }
  clear() {
    this.records = [];
    for (const [key, row] of this.live) {
      if (!row.active) this.live.delete(key);
      else { row.count = 1; row.last = Date.now(); }
    }
    this.render();
  }
  render() {
    try { sessionStorage.setItem('orb-important-live', JSON.stringify([...this.live])); } catch {}
    if (!this.element) return;
    const rows = [...this.live.values(), ...this.records.map(row => ({...row}))];
    // An unresolved missing-ID slot must remain visible even after clearing history.
    for (const slot of this.pendingSlots) {
      const [slave, cell] = slot.split(':');
      const existing = rows.find(row => row.key === `added:S${slave} C${cell}`);
      const pending = {title:`S${slave} C${cell} · 오브 재배치 필요`, detail:'오브를 뺐다가 다시 올려주세요', active:true, severity:'error'};
      if (existing) Object.assign(existing, pending);
      else rows.push(pending);
    }
    rows.sort((a,b) => Number(Boolean(b.active)) - Number(Boolean(a.active)) || b.last - a.last);
    const fragment = document.createDocumentFragment();
    for (const row of rows) {
      const item = document.createElement('div'); item.className = 'issue-row ' + (row.active ? 'issue-active' : 'issue-history');
      const title = document.createElement('strong'); title.textContent = row.title;
      const meta = document.createElement('span');
      meta.textContent = (row.active ? '현재 문제' : row.active === false ? '복구됨 · 발생 이력' : '발생 이력') + (row.count ? ` · ${row.count}회` : '') + (row.last ? ` · 마지막 ${new Date(row.last).toLocaleTimeString('ko-KR', {hour12:false})}` : '');
      const detail = document.createElement('p'); detail.textContent = row.detail || '';
      item.append(title, meta, detail); fragment.append(item);
    }
    if (!rows.length) { const empty = document.createElement('p'); empty.className='issues-empty'; empty.textContent='확인된 주요 이상 없음'; fragment.append(empty); }
    this.element.replaceChildren(fragment);
  }
}
if (typeof module !== 'undefined') module.exports = IssuePanel;
