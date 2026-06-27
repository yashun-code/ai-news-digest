(() => {
  'use strict';

  const app = document.getElementById('app');
  const pageTitle = document.getElementById('page-title');
  const pageSubtitle = document.getElementById('page-subtitle');
  const toast = document.getElementById('toast');
  const navButtons = [...document.querySelectorAll('.nav button')];
  const state = { index: null, digests: new Map(), tipsIndex: null, tips: new Map(), toastTimer: null };

  let deferredInstallPrompt = null;
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredInstallPrompt = e;
    if (location.hash === '#menu') renderMenu();
  });
  window.addEventListener('appinstalled', () => {
    deferredInstallPrompt = null;
    if (location.hash === '#menu') renderMenu();
  });

  const escapeHtml = (value = '') => String(value).replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[char]);

  function renderInlineMarkdown(value = '') {
    return escapeHtml(value)
      .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
      .replace(/\n/g, '<br>');
  }

  function parseDigest(markdown) {
    const lines = markdown.replace(/\r\n?/g, '\n').split('\n');
    const meta = {};
    let cursor = 0;
    if (lines[0] === '---') {
      cursor = 1;
      while (cursor < lines.length && lines[cursor] !== '---') {
        const separator = lines[cursor].indexOf(':');
        if (separator > 0) {
          meta[lines[cursor].slice(0, separator).trim()] = lines[cursor].slice(separator + 1).trim();
        }
        cursor += 1;
      }
      if (lines[cursor] === '---') cursor += 1;
    }

    const items = [];
    let item = null;
    for (; cursor < lines.length; cursor += 1) {
      const line = lines[cursor];
      if (line.startsWith('## ')) {
        if (item) finishItem(item, items);
        item = { title: line.slice(3).trim(), summaryLines: [], kind: '', tag: '', try: '', term: '', relate: '', sources: [] };
      } else if (item) {
        if (line.startsWith('- kind:')) item.kind = line.slice(7).trim();
        else if (line.startsWith('- tag:')) item.tag = line.slice(6).trim();
        else if (line.startsWith('- try:')) item.try = line.slice(6).trim();
        else if (line.startsWith('- term:')) item.term = line.slice(7).trim();
        else if (line.startsWith('- relate:')) item.relate = line.slice(9).trim();
        else if (line.startsWith('- source:')) item.sources.push(line.slice(9).trim());
        else item.summaryLines.push(line);
      }
    }
    if (item) finishItem(item, items);
    return { meta, items };
  }

  function finishItem(item, items) {
    item.summary = item.summaryLines.join('\n').trim().replace(/\n{3,}/g, '\n\n');
    delete item.summaryLines;
    items.push(item);
  }

  function formatDate(dateString, withYear = true) {
    const [year, month, day] = dateString.split('-').map(Number);
    if (!year || !month || !day) return dateString;
    return withYear ? `${year}年${month}月${day}日` : `${month}月${day}日`;
  }

  function isSafeSource(url) {
    return typeof url === 'string' && url.startsWith('https://');
  }

  function isSafeDigestPath(path) {
    return typeof path === 'string' && /^digests\/[A-Za-z0-9_-]+\.md$/.test(path);
  }

  function isSafeTipsPath(path) {
    return typeof path === 'string' && /^tips\/[A-Za-z0-9_-]+\.md$/.test(path);
  }

  async function loadIndex(force = false) {
    if (state.index && !force) return state.index;
    const response = await fetch('./index.json');
    if (!response.ok) throw new Error('index.json could not be loaded');
    const data = await response.json();
    if (!data || !Array.isArray(data.weeks)) throw new Error('index.json is invalid');
    state.index = data;
    return data;
  }

  async function loadDigest(week, force = false) {
    if (!isSafeDigestPath(week.file)) throw new Error('digest path is invalid');
    if (state.digests.has(week.file) && !force) return state.digests.get(week.file);
    const response = await fetch(`./${week.file}`);
    if (!response.ok) throw new Error('digest could not be loaded');
    const digest = parseDigest(await response.text());
    state.digests.set(week.file, digest);
    return digest;
  }

  async function loadTipsIndex(force = false) {
    if (state.tipsIndex && !force) return state.tipsIndex;
    const response = await fetch('./tips_index.json');
    if (!response.ok) throw new Error('tips_index.json could not be loaded');
    const data = await response.json();
    if (!data || !Array.isArray(data.entries)) throw new Error('tips_index.json is invalid');
    state.tipsIndex = data;
    return data;
  }

  async function loadTips(entry, force = false) {
    if (!isSafeTipsPath(entry.file)) throw new Error('tips path is invalid');
    if (state.tips.has(entry.file) && !force) return state.tips.get(entry.file);
    const response = await fetch(`./${entry.file}`);
    if (!response.ok) throw new Error('tips could not be loaded');
    const tips = parseDigest(await response.text());
    state.tips.set(entry.file, tips);
    return tips;
  }

  function setHeader(title, subtitle) {
    pageTitle.textContent = title;
    pageSubtitle.textContent = subtitle;
  }

  function setCurrentNav(route) {
    const active = route === 'week' ? 'home' : route;
    navButtons.forEach((button) => {
      const current = button.dataset.route === active;
      button.classList.toggle('on', current);
      if (current) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });
  }

  function showLoading() {
    app.innerHTML = '<div class="loading" role="status">ニュースを読み込んでいます…</div>';
  }

  function showError(retry) {
    setHeader('AIニュース', '読み込みエラー');
    app.innerHTML = '<div class="error-box"><div>ニュースを読み込めませんでした。<br>通信環境をご確認ください</div><button class="retry" type="button">再読み込み</button></div>';
    app.querySelector('.retry').addEventListener('click', retry);
  }

  function showToast(message, failed = false) {
    toast.textContent = message;
    toast.classList.toggle('fail', failed);
    toast.classList.add('show');
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => toast.classList.remove('show'), 2200);
  }

  function buildPrompt(item) {
    return `次のAIニュースについて、初心者にもわかるように詳しく教えてください。専門用語にはやさしい説明を添えてください。\n\n【見出し】${item.title}\n【要約】${item.summary}`;
  }

  function legacyCopy(text) {
    const textarea = document.createElement('textarea');
    try {
      textarea.value = text;
      textarea.setAttribute('readonly', '');
      textarea.style.position = 'fixed';
      textarea.style.opacity = '0';
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      textarea.setSelectionRange(0, textarea.value.length);
      return document.execCommand('copy');
    } catch (_) {
      return false;
    } finally {
      textarea.remove();
    }
  }

  function finishCopy(button, succeeded, resetLabel = '🤖 AIに詳しく聞く', successMessage = 'コピーしました。ChatGPTやClaudeなどのAIに貼り付けると詳しく教えてもらえます') {
    if (succeeded) {
      button.textContent = '✓ コピー完了';
      button.classList.add('done');
      showToast(successMessage);
    } else {
      button.textContent = '失敗';
      button.classList.add('fail');
      showToast('コピーできませんでした。文章を長押しして選択・コピーしてください', true);
    }
    setTimeout(() => {
      button.textContent = resetLabel;
      button.classList.remove('done', 'fail');
      delete button.dataset.busy;
    }, 2000);
  }

  function copyText(button, text, resetLabel, successMessage) {
    if (button.dataset.busy) return;
    button.dataset.busy = '1';
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      navigator.clipboard.writeText(text)
        .then(() => finishCopy(button, true, resetLabel, successMessage))
        .catch(() => finishCopy(button, legacyCopy(text), resetLabel, successMessage));
    } else {
      finishCopy(button, legacyCopy(text), resetLabel, successMessage);
    }
  }

  function copyPrompt(button, item) {
    copyText(button, buildPrompt(item), '🤖 AIに詳しく聞く', 'コピーしました。ChatGPTやClaudeなどのAIに貼り付けると詳しく教えてもらえます');
  }

  function copyTip(button, item) {
    copyText(button, item.try, '📋 そのまま試す', 'コピーしました。Claude Codeに貼り付けて、そのまま試せます');
  }

  function renderDigest(week, digest) {
    const date = digest.meta.date || week.date;
    const title = digest.meta.title || week.title || '今週のAIニュース';
    const countLabel = digest.items.length ? ` · TOP${digest.items.length}` : '';
    setHeader(title.replace(/\s*TOP\d+$/i, ''), `${formatDate(date)}${countLabel}`);
    const cards = digest.items.map((item, index) => {
      const hasSource = isSafeSource(item.sources[0]);
      const sourceControl = hasSource
        ? `<a class="btn src" href="${escapeHtml(item.sources[0])}" target="_blank" rel="noopener noreferrer" aria-label="出典を見る">🔗 出典</a>`
        : '<button class="btn src" type="button" aria-label="出典リンクは利用できません" disabled>🔗 出典</button>';
      return `<article class="card" data-item="${index}">
        <h2><span class="num">${index + 1}</span><span class="t">${escapeHtml(item.title)}</span></h2>
        <div class="summary">${renderInlineMarkdown(item.summary)}</div>
        ${item.term ? `<div class="term">用語：${renderInlineMarkdown(item.term)}</div>` : ''}
        ${item.relate ? `<div class="rel"><b>🟢</b><span>あなたに関係：${renderInlineMarkdown(item.relate)}</span></div>` : ''}
        <div class="actions">
          <button class="btn copy" type="button" aria-label="このニュースをAIに聞くための文章をコピー">🤖 AIに詳しく聞く</button>
          ${sourceControl}
        </div>
      </article>`;
    }).join('');

    app.innerHTML = `
      <p class="guide">${renderInlineMarkdown(digest.meta.intro || '英語記事や専門用語は読まなくてOK。各項目の **🟢あなたに関係** を確認してください。')}</p>
      ${cards || '<div class="empty">この週のニュースはまだありません。</div>'}
      ${digest.meta.oneliner ? `<div class="closing"><b>今週のひとこと</b><br>${renderInlineMarkdown(digest.meta.oneliner)}</div>` : ''}`;

    app.querySelectorAll('.card').forEach((card) => {
      const item = digest.items[Number(card.dataset.item)];
      card.querySelector('.copy').addEventListener('click', (event) => copyPrompt(event.currentTarget, item));
    });
  }

  async function renderHome(requestedDate = '', force = false) {
    setCurrentNav(requestedDate ? 'week' : 'home');
    showLoading();
    try {
      const index = await loadIndex(force);
      const week = requestedDate ? index.weeks.find((entry) => entry.date === requestedDate) : index.weeks[0];
      if (!week) throw new Error('week not found');
      renderDigest(week, await loadDigest(week, force));
    } catch (_) {
      showError(() => renderHome(requestedDate, true));
    }
  }

  async function renderHistory(force = false) {
    setCurrentNav('history');
    setHeader('履歴', '過去のダイジェスト');
    showLoading();
    try {
      const index = await loadIndex(force);
      const groups = new Map();
      index.weeks.forEach((week) => {
        const key = week.date.slice(0, 7);
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(week);
      });
      let markup = '';
      [...groups.entries()].forEach(([key, weeks]) => {
        const [year, month] = key.split('-').map(Number);
        markup += `<div class="hgroup">${year}年${month}月</div>`;
        markup += weeks.map((week) => {
          const latest = week === index.weeks[0];
          return `<button class="hitem" type="button" data-date="${escapeHtml(week.date)}">
            <span class="hd">${formatDate(week.date, false)} · ${escapeHtml(week.title)}</span>
            <span class="hs">${escapeHtml(week.summary || '')}</span>
            ${latest ? '<span class="hb">最新</span>' : ''}
          </button>`;
        }).join('');
      });
      app.innerHTML = markup || '<div class="empty">履歴はまだありません。</div>';
      app.querySelectorAll('.hitem').forEach((button) => button.addEventListener('click', () => {
        location.hash = `week/${button.dataset.date}`;
      }));
    } catch (_) {
      showError(() => renderHistory(true));
    }
  }

  function tipsSourceControl(item) {
    const source = item.sources.find(isSafeSource);
    return source
      ? `<a class="btn src" href="${escapeHtml(source)}" target="_blank" rel="noopener noreferrer" aria-label="出典を見る">🔗 出典</a>`
      : '<button class="btn src" type="button" aria-label="出典リンクは利用できません" disabled>🔗 出典</button>';
  }

  function renderTipCard(item, index, hero = false) {
    const tryControl = item.try
      ? `<button class="btn copy tip-copy" type="button" aria-label="Claude Codeで試す文章をコピー">📋 そのまま試す</button>`
      : '';
    return `<article class="${hero ? 'lesson-hero' : 'card tip-card'}" data-tip-item="${index}">
      <div class="tip-heading">
        ${item.tag ? `<span class="tag">${escapeHtml(item.tag)}</span>` : ''}
        <h2>${escapeHtml(item.title)}</h2>
      </div>
      <div class="summary">${renderInlineMarkdown(item.summary)}</div>
      ${item.term ? `<div class="term">用語：${renderInlineMarkdown(item.term)}</div>` : ''}
      <div class="actions">${tryControl}${tipsSourceControl(item)}</div>
    </article>`;
  }

  async function renderTips(force = false) {
    setCurrentNav('tips');
    setHeader('使い方', 'Claude Codeを少しずつ覚える');
    app.innerHTML = '<div class="loading" role="status">使い方を読み込んでいます…</div>';
    try {
      const index = await loadTipsIndex(force);
      const entry = index.entries[0];
      if (!entry) throw new Error('tips entry not found');
      const tips = await loadTips(entry, force);
      const date = tips.meta.date || entry.date;
      const theme = tips.meta.theme || entry.theme || 'Claude Code学習';
      const lessonIndex = tips.items.findIndex((item) => item.kind === 'lesson');
      const lesson = lessonIndex >= 0 ? tips.items[lessonIndex] : tips.items[0];
      if (!lesson) throw new Error('lesson not found');
      const cards = tips.items
        .map((item, itemIndex) => ({ item, itemIndex }))
        .filter(({ itemIndex }) => itemIndex !== lessonIndex)
        .map(({ item, itemIndex }) => renderTipCard(item, itemIndex))
        .join('');
      setHeader('使い方', `${formatDate(date)} · ${theme}`);
      app.innerHTML = `
        <p class="guide">${renderInlineMarkdown(tips.meta.intro || entry.summary || 'Claude Codeを、ひとつずつ気楽に試してみましょう。')}</p>
        <section class="lesson-wrap" aria-label="今日のレッスン">
          <div class="lesson-kicker">今日のレッスン</div>
          <div class="lesson-meta"><span class="pill">${escapeHtml(tips.meta.level || '入門')}</span><span class="pill">${escapeHtml(tips.meta.readtime || '約3分')}</span></div>
          ${renderTipCard(lesson, lessonIndex, true)}
        </section>
        ${cards ? `<div class="tips-heading">あわせて知っておくと便利</div>${cards}` : ''}`;
      app.querySelectorAll('[data-tip-item]').forEach((card) => {
        const item = tips.items[Number(card.dataset.tipItem)];
        const button = card.querySelector('.tip-copy');
        if (button) button.addEventListener('click', (event) => copyTip(event.currentTarget, item));
      });
    } catch (_) {
      setHeader('使い方', '読み込みエラー');
      app.innerHTML = '<div class="error-box"><div>使い方を読み込めませんでした。<br>通信環境をご確認ください</div><button class="retry" type="button">再読み込み</button></div>';
      app.querySelector('.retry').addEventListener('click', () => renderTips(true));
    }
  }

  function renderMenu() {
    setCurrentNav('menu');
    setHeader('メニュー', '設定・このアプリについて');
    const installSection = deferredInstallPrompt
      ? `<div class="msec">インストール</div>
    <div class="mrow first" id="install-row"><span class="mi" aria-hidden="true">📲</span><div class="col"><span>ホーム画面に追加</span><span class="desc">アプリのようにすぐ開けます</span></div><button class="btn-install" type="button">追加</button></div>`
      : '';
    app.innerHTML = `
  ${installSection}
  <div class="msec">あなた向け</div>
  <div class="mrow first"><span class="mi" aria-hidden="true">🎯</span><div class="col"><span>自分に合ったテーマに絞る</span><span class="desc">例：Geminiだけ・ビジネス活用だけ など</span></div><span class="soon">準備中</span></div>
  <div class="mrow last"><span class="mi" aria-hidden="true">🔔</span><span>新着のお知らせ</span><span class="ma">準備中</span></div>
  <div class="msec">表示</div>
  <div class="mrow first"><span class="mi" aria-hidden="true">🌙</span><span>テーマ</span><span class="ma">ダーク</span></div>
  <div class="mrow last"><span class="mi" aria-hidden="true">🔠</span><span>文字サイズ</span><span class="ma">標準</span></div>
  <div class="msec">このアプリについて</div>
  <div class="mrow solo"><span class="mi" aria-hidden="true">ℹ️</span><div class="col"><span>AIニュースダイジェスト</span><span class="desc">英語と専門用語を読まずに、今週の動きがわかるアプリ</span></div></div>
  <div class="caption"><span aria-hidden="true">📅</span><span>月・水・金の朝に自動で更新。ホーム画面から最新のダイジェストを確認できます。</span></div>`;
    const installBtn = document.getElementById('install-row')?.querySelector('.btn-install');
    if (installBtn && deferredInstallPrompt) {
      installBtn.addEventListener('click', async () => {
        deferredInstallPrompt.prompt();
        const { outcome } = await deferredInstallPrompt.userChoice;
        if (outcome === 'accepted') deferredInstallPrompt = null;
        renderMenu();
      });
    }
  }

  function route() {
    const hash = location.hash.replace(/^#/, '');
    if (hash === 'history') renderHistory();
    else if (hash === 'tips') renderTips();
    else if (hash === 'menu') renderMenu();
    else if (hash.startsWith('week/')) renderHome(hash.slice(5));
    else renderHome();
  }

  navButtons.forEach((button) => button.addEventListener('click', () => {
    const destination = button.dataset.route;
    if (destination === 'home' && (!location.hash || location.hash === '#home')) renderHome();
    else location.hash = destination;
  }));
  window.addEventListener('hashchange', route);
  route();

  if ('serviceWorker' in navigator) {
    let refreshing = false;
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      if (refreshing) return;
      refreshing = true;
      location.reload();
    });
    window.addEventListener('load', () => navigator.serviceWorker.register('./sw.js').catch(() => {}));
  }
})();
