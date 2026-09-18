chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: 'import-page-to-radar',
    title: '把当前页加入 Research Radar',
    contexts: ['page'],
  });
  chrome.contextMenus.create({
    id: 'import-link-to-radar',
    title: '把这个链接加入 Research Radar',
    contexts: ['link'],
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const url = info.linkUrl || info.pageUrl || tab?.url;
  if (!url) return;

  const data = await chrome.storage.local.get(['base', 'caseId']);
  const base = (data.base || 'http://localhost:8501/api').replace(/\/+$/, '');
  const caseId = data.caseId || '';

  if (!caseId) {
    notify('Research Radar', '请先在扩展弹窗中填写 Case ID');
    return;
  }

  try {
    const resp = await fetch(`${base}/cases/${encodeURIComponent(caseId)}/sources/import`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
    const payload = await resp.json();
    if (!resp.ok) {
      throw new Error(payload.detail || `HTTP ${resp.status}`);
    }
    notify('Research Radar', `已加入：${payload.title}`);
  } catch (e) {
    notify('Research Radar', `导入失败：${e.message}`);
  }
});

function notify(title, message) {
  if (typeof chrome.notifications !== 'undefined') {
    chrome.notifications.create({
      type: 'basic',
      iconUrl: 'icon.png',
      title,
      message,
    });
  } else {
    console.log(`${title}: ${message}`);
  }
}
