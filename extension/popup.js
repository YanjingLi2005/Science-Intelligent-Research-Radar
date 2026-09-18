const DEFAULT_BASE = 'http://localhost:8501/api';
const baseInput = document.getElementById('base');
const caseInput = document.getElementById('caseId');
const caseSelect = document.getElementById('caseSelect');
const importBtn = document.getElementById('importBtn');
const resultDiv = document.getElementById('result');
const recentImportsDiv = document.getElementById('recentImports');
let loadTimer = null;
let latestLoadId = 0;

function getBase() {
  return (baseInput.value.trim() || DEFAULT_BASE).replace(/\/+$/, '');
}

function saveSettings(settings) {
  chrome.storage.local.set(settings);
}

function showResult(message, className = '') {
  resultDiv.textContent = message;
  resultDiv.className = className;
}

function renderRecentImports() {
  chrome.storage.local.get(['recentImports'], (data) => {
    recentImportsDiv.replaceChildren();

    const recentImports = Array.isArray(data.recentImports) ? data.recentImports : [];
    for (const item of recentImports) {
      const entry = document.createElement('div');
      entry.className = 'recent-import';

      const title = document.createElement('div');
      title.className = 'recent-import-title';
      title.textContent = item.title || item.url || '未命名来源';

      const time = document.createElement('div');
      time.className = 'recent-import-time';
      const parsedTime = new Date(item.time);
      time.textContent = Number.isNaN(parsedTime.getTime())
        ? item.time || ''
        : parsedTime.toLocaleString('zh-CN');

      entry.append(title, time);
      recentImportsDiv.appendChild(entry);
    }
  });
}

function saveRecentImport(entry) {
  chrome.storage.local.get(['recentImports'], (data) => {
    const recentImports = Array.isArray(data.recentImports) ? data.recentImports : [];
    chrome.storage.local.set({
      recentImports: [entry, ...recentImports].slice(0, 5),
    }, renderRecentImports);
  });
}

function hideCaseSelect() {
  caseSelect.replaceChildren();
  caseSelect.appendChild(new Option('请选择案例…', ''));
  caseSelect.hidden = true;
  caseSelect.disabled = true;
}

function populateCases(cases, selectedCaseId = '') {
  caseSelect.replaceChildren();
  caseSelect.appendChild(new Option('请选择案例…', ''));

  const normalizedCases = cases
    .map((item) => ({
      id: String(item?.id || '').trim(),
      title: String(item?.title || item?.name || item?.id || '').trim(),
    }))
    .filter((item) => item.id);

  for (const item of normalizedCases) {
    caseSelect.appendChild(new Option(item.title || item.id, item.id));
  }

  const savedCaseId = selectedCaseId || caseInput.value.trim();
  if (normalizedCases.some((item) => item.id === savedCaseId)) {
    caseSelect.value = savedCaseId;
    caseInput.value = savedCaseId;
  }

  if (normalizedCases.length === 0) {
    hideCaseSelect();
    return;
  }

  caseSelect.hidden = false;
  caseSelect.disabled = false;
}

async function loadCases() {
  const base = getBase();
  const loadId = ++latestLoadId;
  saveSettings({ base });
  caseSelect.disabled = true;

  try {
    const response = await fetch(`${base}/cases`, { method: 'GET' });
    let payload = null;
    try {
      payload = await response.json();
    } catch (e) {
      // Keep the HTTP error below useful even when the response is not JSON.
    }

    if (!response.ok) {
      throw new Error(payload?.detail || `HTTP ${response.status}`);
    }

    const cases = Array.isArray(payload)
      ? payload
      : Array.isArray(payload?.cases)
        ? payload.cases
        : null;
    if (!cases) throw new Error('返回格式无效');
    if (loadId !== latestLoadId) return;

    populateCases(cases, caseInput.value.trim());
  } catch (e) {
    if (loadId !== latestLoadId) return;
    hideCaseSelect();
    showResult(`案例加载失败，可手动填写 Case ID：${e.message || '网络请求失败'}`, 'err');
  }
}

function scheduleCaseLoad() {
  clearTimeout(loadTimer);
  loadTimer = setTimeout(loadCases, 300);
}

baseInput.addEventListener('input', () => {
  saveSettings({ base: getBase() });
  scheduleCaseLoad();
});

baseInput.addEventListener('change', loadCases);

caseSelect.addEventListener('change', () => {
  if (!caseSelect.value) return;
  caseInput.value = caseSelect.value;
  saveSettings({ caseId: caseSelect.value });
});

caseInput.addEventListener('input', () => {
  if (caseSelect.value !== caseInput.value.trim()) caseSelect.value = '';
  saveSettings({ caseId: caseInput.value.trim() });
});

// Restore saved settings, then load the available cases for the saved base.
chrome.storage.local.get(['base', 'caseId'], (data) => {
  if (data.base) baseInput.value = data.base;
  if (data.caseId) caseInput.value = data.caseId;
  loadCases();
});

renderRecentImports();

function extractDoiFromUrl(url) {
  const m = url.match(/10\.\d{4,9}\/[^\s?#]+/i);
  return m ? m[0].replace(/[.,;)]$/, '') : '';
}

importBtn.addEventListener('click', async () => {
  const base = getBase();
  const caseId = caseInput.value.trim();
  if (!caseId) {
    showResult('请填写 Case ID', 'err');
    return;
  }

  saveSettings({ base, caseId });

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.url) {
    showResult('无法读取当前页面 URL', 'err');
    return;
  }

  let doi = extractDoiFromUrl(tab.url);
  if (!doi && tab.id != null) {
    try {
      const results = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => {
          const meta = document.querySelector('meta[name="citation_doi"]');
          if (meta && meta.content) return meta.content.trim();
          const text = (document.body && document.body.innerText || '').slice(0, 5000);
          const m = text.match(/10\.\d{4,9}\/[^\s]+/);
          return m ? m[0].replace(/[.,;)]$/, '') : '';
        },
      });
      if (results && results[0] && results[0].result) doi = results[0].result;
    } catch (e) {
      // ignore scripting errors; fall back to URL only
    }
  }

  importBtn.disabled = true;
  showResult('正在解析并导入…');
  try {
    const resp = await fetch(`${base}/cases/${encodeURIComponent(caseId)}/sources/import`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: tab.url, doi }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.detail || `HTTP ${resp.status}`);
    }
    showResult(`已加入雷达：\n${data.title}\n${data.url}`, 'ok');
    saveRecentImport({
      title: data.title || tab.title || tab.url,
      url: data.url || tab.url,
      time: new Date().toISOString(),
    });
  } catch (e) {
    showResult(`导入失败：${e.message}`, 'err');
  } finally {
    importBtn.disabled = false;
  }
});
