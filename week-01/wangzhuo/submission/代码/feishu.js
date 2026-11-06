// shortcut: depends on Feishu's current DOM; use the official API if the layout changes.
(() => {
  const root = document.querySelector('.docx-page-block');
  const box = document.querySelector('.bear-web-x-container');
  if (!root || !box) throw new Error('未找到飞书正文，请检查登录和权限');
  const clean = s => s.replace(/[\u200b\ufeff]/g, '').trim();
  const blocks = [...root.querySelectorAll('[data-block-id]')].filter(e =>
    !e.parentElement.closest('[data-block-type="table"]')
  ).map(e => {
    const type = e.dataset.blockType;
    const zone = [...e.querySelectorAll('[data-zone-id]')].find(z =>
      z.closest('[data-block-id]') === e
    );
    const copy = (zone || e).cloneNode(true);
    copy.querySelectorAll('button,svg,script,style,[data-block-id]').forEach(n => n.remove());
    let html = copy.innerHTML;
    const text = clean(copy.textContent || '');
    const heading = /^heading([1-6])$/.exec(type);
    if (heading) html = `<h${heading[1]}>${html}</h${heading[1]}>`;
    if (type === 'bullet' || type === 'ordered') {
      const list = type === 'bullet' ? 'ul' : 'ol';
      html = `<${list}><li>${html}</li></${list}>`;
    }
    if (type === 'code') html = `<pre>${html}</pre>`;
    if (type === 'table') {
      const table = e.querySelector('table');
      html = table ? table.outerHTML : `<p>[表格布局未提取，仅保留文字]</p><p>${e.innerText.replaceAll('&','&amp;').replaceAll('<','&lt;')}</p>`;
    }
    const supported = /^(text|heading[1-6]|bullet|ordered|code|table|quote|divider)$/.test(type);
    if (!supported) html = `<p>[未完整支持的文档块：${type}]</p>${html}`;
    return {id:e.dataset.blockId, html:html, text:text};
  });
  const result = {
    title:clean(document.querySelector('h1')?.innerText || document.title),
    blocks, bottom:box.scrollTop + box.clientHeight >= box.scrollHeight - 5,
    height:box.scrollHeight
  };
  box.scrollBy(0, Math.max(200, box.clientHeight * 0.7));
  return JSON.stringify(result);
})()
