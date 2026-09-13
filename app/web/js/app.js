// ReadLoops 阅读器
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

let currentArticle = null;
let timerInterval = null;
let timerSeconds = 0;
let timerRunning = false;
let lookupsInSession = 0;
let highlightsInSession = 0;

const api = {
  async get(url) { const r = await fetch(url); return r.json(); },
  async post(url, data) {
    const r = await fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(data || {})
    });
    return r.json();
  },
  async delete(url) {
    const r = await fetch(url, { method: 'DELETE' });
    return r.json();
  }
};

/**
 * 应用内确认对话框（替代原生 confirm）。
 *
 * 为什么不用原生 confirm()：在 Chrome「应用模式」（--app=）与已安装的 PWA 中，
 * 原生弹窗可能被浏览器拦截或用户曾勾选「阻止此页面创建更多对话框」，
 * 此时 confirm() 会静默返回 false —— 表现为「点了删除没反应」。
 * 用应用内自绘对话框可彻底规避，且样式与应用一致。
 *
 * @returns {Promise<boolean>} 用户是否确认
 */
function confirmDialog(message, { okText = '确定', cancelText = '取消', danger = false } = {}) {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'app-dialog-overlay';
    overlay.innerHTML =
      '<div class="app-dialog" role="dialog" aria-modal="true">' +
        '<div class="app-dialog-message">' + escapeHtml(message) + '</div>' +
        '<div class="app-dialog-actions">' +
          '<button class="toolbar-btn" data-act="cancel">' + escapeHtml(cancelText) + '</button>' +
          '<button class="toolbar-btn ' + (danger ? 'danger' : 'primary') + '" data-act="ok">' +
            escapeHtml(okText) +
          '</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);

    const okBtn = overlay.querySelector('[data-act="ok"]');
    const done = (val) => {
      overlay.remove();
      document.removeEventListener('keydown', onKey);
      resolve(val);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') done(false);
      else if (e.key === 'Enter') done(true);
    };

    okBtn.addEventListener('click', () => done(true));
    overlay.querySelector('[data-act="cancel"]').addEventListener('click', () => done(false));
    overlay.addEventListener('click', (e) => { if (e.target === overlay) done(false); });
    document.addEventListener('keydown', onKey);
    okBtn.focus();
  });
}

/** 应用内提示框（替代原生 alert）。 */
function showAlert(message) {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'app-dialog-overlay';
    overlay.innerHTML =
      '<div class="app-dialog" role="dialog" aria-modal="true">' +
        '<div class="app-dialog-message">' + escapeHtml(message) + '</div>' +
        '<div class="app-dialog-actions">' +
          '<button class="toolbar-btn primary" data-act="ok">知道了</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);

    const okBtn = overlay.querySelector('[data-act="ok"]');
    const done = () => { overlay.remove(); resolve(); };
    okBtn.addEventListener('click', done);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) done(); });
    okBtn.focus();
  });
}

function toast(msg, duration = 2000) {
  const t = document.createElement('div');
  t.className = 'toast';
  t.textContent = msg;
  document.body.appendChild(t);
  requestAnimationFrame(() => t.classList.add('show'));
  setTimeout(() => {
    t.classList.remove('show');
    setTimeout(() => t.remove(), 300);
  }, duration);
}

// 主题
$('#themeSelect').addEventListener('change', (e) => {
  document.documentElement.setAttribute('data-theme', e.target.value);
  localStorage.setItem('yuedu-theme', e.target.value);
});
const savedTheme = localStorage.getItem('yuedu-theme');
if (savedTheme) {
  document.documentElement.setAttribute('data-theme', savedTheme);
  $('#themeSelect').value = savedTheme;
}

// 侧边栏
$('#toggleSidebar').addEventListener('click', () => {
  // 用户手动点击折叠按钮，退出自动侧边栏模式
  autoSidebar = false;
  const app = document.querySelector('.app');
  if (app) app.classList.remove('auto-sidebar-mode');
  const sidebar = $('#sidebar');
  sidebar.classList.remove('sidebar-visible');
  sidebar.classList.toggle('collapsed');
});

// 自动侧边栏模式：和文章互动时收起侧边栏
const readerContainer = $('#readerContainer');
function autoCollapseSidebar() {
  if (autoSidebar) {
    $('#sidebar').classList.remove('sidebar-visible');
  }
  // 文章互动自动开启专注模式
  tryAutoFocus();
}
readerContainer.addEventListener('click', autoCollapseSidebar);
readerContainer.addEventListener('scroll', autoCollapseSidebar);
document.addEventListener('mouseup', (e) => {
  // 划词结束时，如果在阅读区内，收起侧边栏
  if (autoSidebar && readerContainer.contains(e.target)) {
    const sel = window.getSelection();
    if (sel && sel.toString().trim()) {
      // 有划词，不立即收起（等查词面板操作完），但延迟收起
      setTimeout(() => {
        if (autoSidebar) $('#sidebar').classList.remove('sidebar-visible');
      }, 1500);
    } else {
      autoCollapseSidebar();
    }
  }
  // 划词也触发自动专注
  if (readerContainer.contains(e.target)) {
    tryAutoFocus();
  }
});
// 专注模式
let focusMode = false;
let focusStartTime = 0;
let focusTotalTime = 0;
let autoFocusTriggered = false; // 防止重复触发自动专注

$('#focusBtn').addEventListener('click', toggleFocusMode);

// 水纹扩散引导效果 — feTurbulence像素扭曲+涟漪环
let rippleAnim = null;

function triggerRipple() {
  const reader = document.querySelector('.reader');
  const container = $('#rippleContainer');
  const turbulence = document.getElementById('ripple-turbulence');
  const displace = document.getElementById('ripple-displace');
  if (!reader || !container || !turbulence || !displace) return;
  
  // 清除之前的动画
  if (rippleAnim) cancelAnimationFrame(rippleAnim);
  
  container.innerHTML = '';
  
  // 创建涟漪环（从顶部中心扩散）
  const ring = document.createElement('div');
  ring.className = 'ripple-wave';
  ring.style.left = '50%';
  ring.style.top = '0';
  container.appendChild(ring);
  
  // 滤镜只作用于文章区域，避免整个界面色差
  reader.style.filter = 'url(#ripple-distort)';
  
  // 3秒动画：scale从0→峰值→0，baseFrequency流动模拟水波
  const startTime = performance.now();
  const duration = 3000;
  const maxScale = 20;
  
  function animate(now) {
    const elapsed = now - startTime;
    const progress = Math.min(elapsed / duration, 1);
    
    // scale：sin曲线，最后15%更平缓地衰减到0，避免色差跳变
    let scale;
    if (progress < 0.85) {
      scale = Math.sin((progress / 0.85) * Math.PI * 0.5) * maxScale;
    } else {
      const fadeProgress = (progress - 0.85) / 0.15;
      scale = maxScale * (1 - fadeProgress) * 0.5;
    }
    displace.setAttribute('scale', scale.toString());
    
    // baseFrequency：缓慢流动，模拟水波荡漾
    const freq = 0.012 + Math.sin(progress * Math.PI * 2.5) * 0.006;
    turbulence.setAttribute('baseFrequency', freq.toString());
    
    if (progress < 1) {
      rippleAnim = requestAnimationFrame(animate);
    } else {
      // scale归零后等待一帧，再清除filter（.app已常驻合成层，无切换色差）
      displace.setAttribute('scale', '0');
      turbulence.setAttribute('baseFrequency', '0.012');
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          reader.style.filter = '';
          container.innerHTML = '';
        });
      });
      rippleAnim = null;
    }
  }
  
  rippleAnim = requestAnimationFrame(animate);
}

function toggleFocusMode() {
  focusMode = !focusMode;
  document.querySelector('.app').classList.toggle('focus-mode', focusMode);
  if (focusMode) {
    focusStartTime = Date.now();
    $('#focusBtn').textContent = '退出专注';
    $('#focusBtn').classList.add('active');
    toast('已进入专注模式');
    // 触发水纹扩散引导
    setTimeout(triggerRipple, 300);
  } else {
    const duration = Math.round((Date.now() - focusStartTime) / 1000);
    focusTotalTime += duration;
    $('#focusBtn').textContent = '专注';
    $('#focusBtn').classList.remove('active');
    const mins = Math.floor(duration / 60);
    const secs = duration % 60;
    toast(`本次专注 ${mins}分${secs}秒`);
  }
}

// 文章互动自动开启专注模式
function tryAutoFocus() {
  if (focusMode || autoFocusTriggered || !currentArticle) return;
  autoFocusTriggered = true;
  toggleFocusMode();
}

// 专注模式下鼠标移到顶部显示工具栏
const focusTopTrigger = $('#focusTopTrigger');
let toolbarHoverTimer = null;
focusTopTrigger.addEventListener('mouseenter', () => {
  if (!focusMode) return;
  clearTimeout(toolbarHoverTimer);
  $('.toolbar').classList.add('show-on-hover');
});
focusTopTrigger.addEventListener('mouseleave', () => {
  if (!focusMode) return;
  toolbarHoverTimer = setTimeout(() => {
    $('.toolbar').classList.remove('show-on-hover');
  }, 500);
});
// 工具栏本身hover时保持显示
document.querySelector('.toolbar').addEventListener('mouseenter', () => {
  if (focusMode) {
    clearTimeout(toolbarHoverTimer);
    $('.toolbar').classList.add('show-on-hover');
  }
});
document.querySelector('.toolbar').addEventListener('mouseleave', () => {
  if (focusMode) {
    toolbarHoverTimer = setTimeout(() => {
      $('.toolbar').classList.remove('show-on-hover');
    }, 500);
  }
});

// 专注模式下鼠标移到左侧显示侧边栏
const focusLeftTrigger = $('#focusLeftTrigger');
let sidebarHoverTimer = null;
focusLeftTrigger.addEventListener('mouseenter', () => {
  if (focusMode) {
    clearTimeout(sidebarHoverTimer);
    $('#sidebar').classList.add('show-on-hover');
  } else if (autoSidebar) {
    // 自动侧边栏模式：鼠标移到左边缘，展开侧边栏
    clearTimeout(sidebarHoverTimer);
    $('#sidebar').classList.add('sidebar-visible');
  }
});
focusLeftTrigger.addEventListener('mouseleave', () => {
  if (focusMode) {
    sidebarHoverTimer = setTimeout(() => {
      $('#sidebar').classList.remove('show-on-hover');
    }, 500);
  } else if (autoSidebar) {
    // 自动侧边栏模式：鼠标离开左边缘，延迟收起
    sidebarHoverTimer = setTimeout(() => {
      if (autoSidebar) $('#sidebar').classList.remove('sidebar-visible');
    }, 300);
  }
});
$('#sidebar').addEventListener('mouseenter', () => {
  if (focusMode) {
    clearTimeout(sidebarHoverTimer);
    $('#sidebar').classList.add('show-on-hover');
  } else if (autoSidebar) {
    // 鼠标移入侧边栏，取消收起
    clearTimeout(sidebarHoverTimer);
  }
});
$('#sidebar').addEventListener('mouseleave', () => {
  if (focusMode) {
    sidebarHoverTimer = setTimeout(() => {
      $('#sidebar').classList.remove('show-on-hover');
    }, 500);
  } else if (autoSidebar) {
    // 鼠标离开侧边栏，延迟收起
    sidebarHoverTimer = setTimeout(() => {
      if (autoSidebar) $('#sidebar').classList.remove('sidebar-visible');
    }, 300);
  }
});

// 键盘快捷键
document.addEventListener('keydown', (e) => {
  // Esc 退出专注
  if (e.key === 'Escape' && focusMode) {
    toggleFocusMode();
    return;
  }
  // F 切换专注（不在输入框中时）
  if (e.key === 'f' && !focusMode && !['INPUT','TEXTAREA'].includes(e.target.tagName)) {
    e.preventDefault();
    toggleFocusMode();
    return;
  }
  // Space 暂停/继续计时（不在输入框中时）
  if (e.key === ' ' && !['INPUT','TEXTAREA'].includes(e.target.tagName)) {
    e.preventDefault();
    toggleTimer();
  }
});

// 计时器
function formatTime(s) {
  const m = Math.floor(s / 60).toString().padStart(2, '0');
  const sec = (s % 60).toString().padStart(2, '0');
  return `${m}:${sec}`;
}
function startTimer() {
  if (timerRunning) return;
  timerRunning = true;
  $('#timerBtn').textContent = '暂停 [空格]';
  timerInterval = setInterval(() => {
    timerSeconds++;
    $('#timerDisplay').textContent = formatTime(timerSeconds);
  }, 1000);
}
function pauseTimer() {
  timerRunning = false;
  clearInterval(timerInterval);
  $('#timerBtn').textContent = '继续 [空格]';
}
$('#timerBtn').addEventListener('click', () => timerRunning ? pauseTimer() : startTimer());

document.addEventListener('keydown', (e) => {
  if (e.code === 'Space' && !['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName)) {
    e.preventDefault();
    timerRunning ? pauseTimer() : startTimer();
  }
  if (e.key === 'Escape') { hideLookupPanel(); closeSettings(); }
});

// 鼠标离开窗口自动暂停计时
document.addEventListener('mouseleave', () => {
  if (timerRunning) pauseTimer();
});
// 窗口失焦也暂停
window.addEventListener('blur', () => {
  if (timerRunning) pauseTimer();
});

// 生成文章
// 生成等待话术
const GENERATING_PHRASES = [
  '正在参考历年四级真题...',
  '正在分析您的词汇掌握程度...',
  '正在提取高频短语和搭配...',
  '正在构建文章结构...',
  '正在融入目标生词...',
  '正在润色语句节奏...',
  '即将完成...',
];
let phraseInterval = null;
let typewriterTimer = null;
let typewriterActive = false;
let isGenerating = false;
let generatingActive = false;
let autoSidebar = false; // 自动侧边栏模式：生成文章后启用，互动收回，边缘展开

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function runGeneratingPhrases() {
  generatingActive = true;
  let idx = 0;
  while (generatingActive) {
    const el = $('#generatingText');
    if (!el) break;
    // 淡出
    el.style.opacity = '0';
    await sleep(250);
    if (!generatingActive) break;
    // 切换文字
    idx = (idx + 1) % GENERATING_PHRASES.length;
    el.textContent = GENERATING_PHRASES[idx];
    // 淡入
    el.style.opacity = '1';
    await sleep(1100);
  }
}

function startGeneratingAnimation() {
  stopGeneratingAnimation();
  generatingActive = true;
  // 显示动态等待界面
  $('#reader').innerHTML = `
    <div class="generating-container">
      <div class="generating-spinner"></div>
      <div class="generating-text" id="generatingText">正在参考历年四级真题...</div>
      <div class="generating-subtext">ReadLoops 正在为你锻造专属阅读材料</div>
    </div>
  `;
  // 启动话术轮播（异步循环，不阻塞）
  runGeneratingPhrases();
}

function stopGeneratingAnimation() {
  generatingActive = false;
  if (phraseInterval) {
    clearInterval(phraseInterval);
    phraseInterval = null;
  }
}

// 打字机效果
function typewriterRender(article) {
  const reader = $('#reader');
  const paragraphs = article.content.split('\n').filter(p => p.trim());
  reader.innerHTML = `<h1 class="typewriter-title">${escapeHtml(article.title)}</h1>`;

  typewriterActive = true;
  let paraIdx = 0;
  let charIdx = 0;
  let currentP = null;

  function finish() {
    typewriterActive = false;
    typewriterTimer = null;
    if (currentP) currentP.classList.remove('typing');
    $('#articleMeta').textContent = `${article.word_count} 词 · ${article.new_word_count} 生词`;
    toast('文章已生成');
  }

  function typeNext() {
    if (!typewriterActive) return;

    if (paraIdx >= paragraphs.length) {
      finish();
      return;
    }

    if (!currentP || charIdx === 0) {
      if (currentP) currentP.classList.remove('typing');
      currentP = document.createElement('p');
      currentP.classList.add('typing');
      reader.appendChild(currentP);
    }

    if (charIdx < paragraphs[paraIdx].length) {
      currentP.textContent += paragraphs[paraIdx][charIdx];
      charIdx++;
      $('#readerContainer').scrollTop = $('#readerContainer').scrollHeight;
      typewriterTimer = setTimeout(typeNext, 16);
    } else {
      currentP.classList.remove('typing');
      paraIdx++;
      charIdx = 0;
      currentP = null;
      typewriterTimer = setTimeout(typeNext, 120);
    }
  }
  typeNext();
}

function stopTypewriter() {
  typewriterActive = false;
  if (typewriterTimer) {
    clearTimeout(typewriterTimer);
    typewriterTimer = null;
  }
}

$('#generateBtn').addEventListener('click', async () => {
  if (isGenerating) return;

  // 【最优先】立即收起侧边栏，在任何 await 之前，确保一定执行
  autoSidebar = true;
  document.querySelector('.app').classList.add('auto-sidebar-mode');
  $('#sidebar').classList.remove('sidebar-visible');
  $('#sidebar').classList.add('collapsed');
  // 重置自动专注触发，新文章可以再次触发
  autoFocusTriggered = false;

  // 切换到阅读视图
  $$('.nav-item').forEach(i => i.classList.remove('active'));
  document.querySelector('[data-view="reader"]').classList.add('active');
  $('#reader').classList.remove('wide-view');

  // 保存上一次阅读会话（失败不影响后续流程）
  try {
    if (currentArticle && timerSeconds > 0) {
      await api.post('/api/reading/session', {
        article_id: currentArticle.id, duration_seconds: timerSeconds,
        lookups: lookupsInSession, highlights: highlightsInSession
      });
    }
  } catch (e) { console.log('保存阅读会话失败:', e); }

  timerSeconds = 0; lookupsInSession = 0; highlightsInSession = 0;
  $('#timerDisplay').textContent = '00:00';
  pauseTimer();

  isGenerating = true;
  $('#generateStatus').innerHTML = '<span class="loading"></span> 生成中';
  $('#generateBtn').disabled = true;

  // 启动生成等待动画
  startGeneratingAnimation();

  try {
    const article = await api.post('/api/articles/generate');
    if (article.detail) throw new Error(article.detail);
    currentArticle = article;
    isGenerating = false;
    stopGeneratingAnimation();
    $('#generateStatus').textContent = '';
    $('#generateBtn').disabled = false;
    // 再次确保侧边栏收起
    document.querySelector('.app').classList.add('auto-sidebar-mode');
    $('#sidebar').classList.remove('sidebar-visible');
    // 如果当前在阅读页面，先淡出等待界面，再播放打字机
    const activeView = document.querySelector('.nav-item.active')?.dataset.view;
    if (activeView === 'reader') {
      const container = $('.generating-container');
      if (container) {
        container.style.transition = 'opacity 0.25s ease';
        container.style.opacity = '0';
        await sleep(250);
      }
      typewriterRender(article);
    }
  } catch (err) {
    isGenerating = false;
    stopGeneratingAnimation();
    $('#generateStatus').textContent = '';
    $('#generateBtn').disabled = false;
    // 生成失败，恢复侧边栏
    autoSidebar = false;
    document.querySelector('.app').classList.remove('auto-sidebar-mode');
    $('#sidebar').classList.remove('collapsed');
    const activeView = document.querySelector('.nav-item.active')?.dataset.view;
    if (activeView === 'reader') {
      $('#reader').innerHTML = `<div class="empty"><h2>生成失败</h2><p>请检查 AI 配置</p></div>`;
    }
    toast('生成失败，请检查 AI 配置');
    console.error(err);
  }
});

function renderArticle(article) {
  $('#reader').innerHTML = `<h1>${escapeHtml(article.title)}</h1>` +
    article.content.split('\n').filter(p => p.trim()).map(p => `<p>${escapeHtml(p)}</p>`).join('');
  $('#readerContainer').scrollTop = 0;
  // 恢复该文章的高亮记录
  restoreHighlights(article.id);
}

async function restoreHighlights(articleId) {
  if (!articleId) return;
  try {
    const highlights = await api.get(`/api/reading/highlights/${articleId}`);
    if (!highlights || !highlights.length) return;
    const reader = $('#reader');
    highlights.forEach(h => {
      if (!h.text) return;
      // 遍历所有文本节点，找到匹配的文本并用mark包裹
      const walker = document.createTreeWalker(reader, NodeFilter.SHOW_TEXT, null);
      let node;
      while ((node = walker.nextNode())) {
        const idx = node.nodeValue.indexOf(h.text);
        if (idx !== -1 && !node.parentElement.closest('mark')) {
          const range = document.createRange();
          range.setStart(node, idx);
          range.setEnd(node, idx + h.text.length);
          const mark = document.createElement('mark');
          try {
            range.surroundContents(mark);
          } catch(e) {}
          break;
        }
      }
    });
  } catch (err) {
    console.log('恢复高亮失败:', err);
  }
}
function escapeHtml(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

// 划词查词
const lookupPanel = $('#lookupPanel');
let currentSelection = '';

document.addEventListener('mouseup', (e) => {
  if (lookupPanel.contains(e.target)) return;
  const sel = window.getSelection();
  const text = sel.toString().trim();
  if (!text || text.length > 60 || !/[a-zA-Z]/.test(text)) { hideLookupPanel(); return; }
  if (!$('#reader').contains(sel.anchorNode)) { hideLookupPanel(); return; }
  currentSelection = text;
  showLookupPanel(e.clientX, e.clientY, text);
});

async function showLookupPanel(x, y, word) {
  lookupPanel.style.left = Math.min(x + 12, window.innerWidth - 320) + 'px';
  lookupPanel.style.top = Math.min(y + 12, window.innerHeight - 200) + 'px';
  lookupPanel.classList.add('show');
  $('#lpWord').textContent = word;
  $('#lpPhonetic').textContent = '';
  $('#lpMeaning').textContent = '查询中...';
  if (!timerRunning && currentArticle) startTimer();
  try {
    const result = await api.get(`/api/words/lookup/${encodeURIComponent(word)}`);
    $('#lpPhonetic').textContent = result.phonetic || '';
    $('#lpMeaning').textContent = result.meaning || '未找到释义';
    lookupPanel.dataset.wordId = result.word_id || '';
    if (result.word_id) {
      lookupsInSession++;
      const aid = currentArticle ? currentArticle.id : 0;
      api.post(`/api/reading/lookup?word_id=${result.word_id}&article_id=${aid}&context=${encodeURIComponent(currentSelection)}`)
        .catch(e => console.log('记录查词失败:', e));
      // 刷新右侧查词历史
      if (typeof loadLookupHistory === 'function') loadLookupHistory();
    }
  } catch (err) { $('#lpMeaning').textContent = '查询失败'; }
}
function hideLookupPanel() { lookupPanel.classList.remove('show'); }

$('#lpPronounce').addEventListener('click', () => {
  const utter = new SpeechSynthesisUtterance($('#lpWord').textContent);
  utter.lang = 'en-US'; utter.rate = 0.9;
  speechSynthesis.speak(utter);
});
$('#lpHighlight').addEventListener('click', () => {
  const sel = window.getSelection();
  if (sel.rangeCount > 0) {
    const range = sel.getRangeAt(0);
    const mark = document.createElement('mark');
    try { range.surroundContents(mark); } catch(e) {}
    highlightsInSession++;
    if (currentArticle) {
      api.post('/api/reading/highlight', {
        article_id: currentArticle.id, text: currentSelection,
        word_id: lookupPanel.dataset.wordId ? parseInt(lookupPanel.dataset.wordId) : null,
        color: 'yellow'
      });
    }
    toast('已高亮');
  }
  hideLookupPanel();
});
$('#lpAdd').addEventListener('click', async () => {
  const word = $('#lpWord').textContent;
  await api.post('/api/words/add', { word, meaning: $('#lpMeaning').textContent, context: currentSelection });
  toast(`已添加「${word}」`);
  hideLookupPanel();
});
// 点击已高亮的文本取消高亮
document.addEventListener('click', (e) => {
  const mark = e.target.closest('mark');
  if (mark && $('#reader').contains(mark)) {
    e.preventDefault();
    e.stopPropagation();
    const text = mark.textContent;
    // unwrap mark 标签，恢复普通文本
    const parent = mark.parentNode;
    while (mark.firstChild) parent.insertBefore(mark.firstChild, mark);
    parent.removeChild(mark);
    parent.normalize();
    // 从数据库删除高亮记录
    if (currentArticle) {
      api.delete(`/api/reading/highlight?article_id=${currentArticle.id}&text=${encodeURIComponent(text)}`)
        .catch(err => console.log('删除高亮失败:', err));
    }
    toast('已取消高亮');
  }
});
document.addEventListener('mousedown', (e) => {
  if (!lookupPanel.contains(e.target) && !$('#reader').contains(e.target)) hideLookupPanel();
});

// 添加生词
$('#addWordBtn').addEventListener('click', () => {
  // 专注模式下打开生词本，自动退出专注模式
  if (focusMode) toggleFocusMode();
  $$('.nav-item').forEach(i => i.classList.remove('active'));
  document.querySelector('[data-view="vocab"]').classList.add('active');
  loadVocab();
});

// 设置面板
const settingsModal = $('#settingsModal');
$('#settingsBtn').addEventListener('click', openSettings);
$('#settingsCancel').addEventListener('click', closeSettings);
settingsModal.addEventListener('click', (e) => { if (e.target === settingsModal) closeSettings(); });

// 服务商预设
const PROVIDER_PRESETS = {
  deepseek: { baseUrl: 'https://api.deepseek.com', model: 'deepseek-flash' },
  opencode: { baseUrl: 'https://opencode.ai/zen/v1', model: 'big-pickle' },
  openai: { baseUrl: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
  custom: { baseUrl: '', model: '' },
};

$('#settingProvider').addEventListener('change', (e) => {
  const preset = PROVIDER_PRESETS[e.target.value];
  if (preset) {
    $('#settingBaseUrl').value = preset.baseUrl;
    if (preset.model) {
      $('#settingModel').value = preset.model;
      $('#settingModelCustom').style.display = 'none';
    }
  }
});

$('#settingModel').addEventListener('change', (e) => {
  $('#settingModelCustom').style.display = e.target.value === 'custom' ? 'block' : 'none';
});

async function openSettings() {
  try {
    const s = await api.get('/api/settings');
    $('#settingBaseUrl').value = s.ai_base_url || '';
    $('#settingApiKey').value = s.ai_api_key || '';
    const model = s.ai_model || '';
    const modelSelect = $('#settingModel');
    const customInput = $('#settingModelCustom');
    if ([...modelSelect.options].some(o => o.value === model)) {
      modelSelect.value = model;
      customInput.style.display = 'none';
    } else {
      modelSelect.value = 'custom';
      customInput.value = model;
      customInput.style.display = 'block';
    }
  } catch(e) {}
  $('#testResult').className = 'test-result';
  $('#testResult').textContent = '';
  settingsModal.classList.add('show');
}
function closeSettings() { settingsModal.classList.remove('show'); }

// API 测试按钮
$('#testApiBtn').addEventListener('click', async () => {
  const btn = $('#testApiBtn');
  const result = $('#testResult');
  btn.disabled = true;
  btn.textContent = '测试中...';
  result.className = 'test-result';
  result.textContent = '';
  try {
    const resp = await fetch('/api/settings/test', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        ai_base_url: $('#settingBaseUrl').value,
        ai_api_key: $('#settingApiKey').value,
        ai_model: getCurrentModel()
      })
    });
    const data = await resp.json();
    if (data.ok) {
      result.className = 'test-result success';
      result.textContent = `连接成功 · 模型: ${data.model || '未知'}`;
    } else {
      result.className = 'test-result error';
      result.textContent = `连接失败: ${data.error || '未知错误'}`;
    }
  } catch (err) {
    result.className = 'test-result error';
    result.textContent = `连接失败: ${err.message}`;
  }
  btn.disabled = false;
  btn.textContent = '测试连接';
});

function getCurrentModel() {
  const sel = $('#settingModel').value;
  return sel === 'custom' ? $('#settingModelCustom').value.trim() : sel;
}

$('#settingsSave').addEventListener('click', async () => {
  await api.post('/api/settings', {
    ai_base_url: $('#settingBaseUrl').value,
    ai_api_key: $('#settingApiKey').value,
    ai_model: getCurrentModel(),
  });
  toast('设置已保存');
  closeSettings();
});

// 导航
// 页面切换：淡出 → 替换内容 → 淡入
let isSwitching = false;
function switchPage(renderFn) {
  if (isSwitching) return;
  isSwitching = true;
  // 切换页面时停止打字机
  stopTypewriter();
  const reader = $('#reader');
  // 1. 淡出（只用opacity，减少重绘）
  reader.classList.add('page-fading');
  setTimeout(() => {
    // 2. 替换内容
    renderFn();
    // 根据当前视图决定是否宽布局
    const activeView = document.querySelector('.nav-item.active')?.dataset.view;
    if (activeView === 'stats' || activeView === 'articles' || activeView === 'vocab') {
      reader.classList.add('wide-view');
    } else {
      reader.classList.remove('wide-view');
    }
    // 专注模式服务于「沉浸阅读」，只在阅读页显示入口；
    // 生词本 / 历史文章 / 测试 / 统计等页面隐藏，避免误触无意义的全屏。
    const focusBtn = $('#focusBtn');
    if (focusBtn) focusBtn.style.display = (activeView === 'reader') ? '' : 'none';
    // 3. 淡入
    reader.classList.remove('page-fading');
    reader.classList.remove('page-enter');
    void reader.offsetWidth; // 强制重排
    reader.classList.add('page-enter');
    $('#readerContainer').scrollTop = 0;
    setTimeout(() => {
      reader.classList.remove('page-enter');
      isSwitching = false;
    }, 450);
  }, 220);
}

$$('.nav-item').forEach(item => {
  item.addEventListener('click', () => {
    if (isSwitching) return;
    // 专注模式下切换到非阅读页，自动退出专注模式
    const view = item.dataset.view;
    if (focusMode && view !== 'reader') {
      toggleFocusMode();
    }
    $$('.nav-item').forEach(i => i.classList.remove('active'));
    item.classList.add('active');
    switchPage(() => {
      if (view === 'reader') {
        if (isGenerating) {
          // 正在生成中，显示等待动画
          startGeneratingAnimation();
        } else {
          // 每次进入阅读页都是待生成空状态
          currentArticle = null;
          autoSidebar = false;
          const app = document.querySelector('.app');
          if (app) app.classList.remove('auto-sidebar-mode');
          const sidebar = $('#sidebar');
          sidebar.classList.remove('collapsed');
          sidebar.classList.remove('sidebar-visible');
          $('#reader').innerHTML = `<div class="empty"><h2>ReadLoops</h2><p>点击「生成文章」开始阅读训练</p></div>`;
        }
      } else if (view === 'articles') loadArticlesList();
      else if (view === 'vocab') loadVocab();
      else if (view === 'test') loadTest();
      else if (view === 'stats') loadStats();
    });
  });
});

async function loadArticlesList() {
  const articles = await api.get('/api/articles/?limit=30');
  $('#reader').innerHTML = '<h1>历史文章</h1><div class="article-list">' +
    (articles.length === 0 ? '<p style="color:var(--text-secondary)">还没有文章</p>' :
    articles.map(a =>
      `<div class="article-item" data-id="${a.id}">
        <div class="article-item-content">
          <div class="title">${escapeHtml(a.title)}</div>
          <div class="meta">${a.word_count} 词 · ${a.new_word_count} 生词 · ${new Date(a.created_at * 1000).toLocaleString('zh-CN', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'})}</div>
        </div>
        <button class="article-delete-btn" data-id="${a.id}" title="删除文章">×</button>
        <div class="article-detail-popup" data-id="${a.id}"></div>
      </div>`
    ).join('')) + '</div>';

  // 点击文章打开
  $$('.article-item-content').forEach(item => {
    item.addEventListener('click', async () => {
      const articleItem = item.closest('.article-item');
      const article = await api.get(`/api/articles/${articleItem.dataset.id}`);
      currentArticle = article;
      $$('.nav-item').forEach(i => i.classList.remove('active'));
      document.querySelector('[data-view="reader"]').classList.add('active');
      switchPage(() => {
        renderArticle(article);
        $('#articleMeta').textContent = `${article.word_count} 词 · ${article.new_word_count} 生词`;
      });
    });
  });

  // 删除按钮
  $$('.article-delete-btn').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const id = btn.getAttribute('data-id');
      console.log('删除文章 id:', id, typeof id);
      if (!id || id === 'undefined') { await showAlert('文章ID无效'); return; }
      if (!await confirmDialog('确定删除这篇文章吗？', { danger: true })) return;
      try {
        const url = window.location.origin + '/api/articles/' + id;
        console.log('删除URL:', url);
        const r = await fetch(url, { method: 'DELETE' });
        const result = await r.json();
        console.log('删除结果:', result);
        btn.closest('.article-item').remove();
        // 如果删除的是当前正在阅读的文章，清除 currentArticle
        if (currentArticle && String(currentArticle.id) === String(id)) {
          currentArticle = null;
        }
        toast('文章已删除');
      } catch(err) {
        console.error('删除错误:', err);
        await showAlert('删除失败: ' + err.message);
      }
    });
  });

  // 悬停显示详情
  let detailTimeout;
  $$('.article-item').forEach(item => {
    item.addEventListener('mouseenter', async () => {
      const id = item.dataset.id;
      const popup = item.querySelector('.article-detail-popup');
      clearTimeout(detailTimeout);
      detailTimeout = setTimeout(async () => {
        if (popup.dataset.loaded) return;
        try {
          const details = await api.get(`/api/articles/${id}/details`);
          popup.innerHTML = renderArticleDetail(details);
          popup.dataset.loaded = 'true';
        } catch(err) {
          popup.innerHTML = '<div style="padding:12px;color:var(--text-tertiary);">加载详情失败</div>';
        }
      }, 300);
    });
    item.addEventListener('mouseleave', () => {
      clearTimeout(detailTimeout);
    });
  });
}

function renderArticleDetail(details) {
  const topWords = details.top_words || [];
  const examSim = details.exam_similarity || [];
  const targetWords = details.target_words || [];

  return `
    <div class="article-detail-content">
      <div class="detail-section">
        <div class="detail-label">高频词</div>
        <div class="detail-tags">
          ${topWords.slice(0, 8).map(([w, c]) => `<span class="detail-tag">${w} <span class="tag-count">${c}</span></span>`).join('')}
        </div>
      </div>
      <div class="detail-section">
        <div class="detail-label">目标生词 (${targetWords.length})</div>
        <div class="detail-tags">
          ${targetWords.slice(0, 10).map(w => `<span class="detail-tag target">${w}</span>`).join('')}
          ${targetWords.length > 10 ? `<span class="detail-tag more">+${targetWords.length - 10}</span>` : ''}
        </div>
      </div>
      ${examSim.length > 0 ? `
      <div class="detail-section">
        <div class="detail-label">相似真题</div>
        <div class="exam-sim-list">
          ${examSim.map(s => `
            <div class="exam-sim-item">
              <span class="exam-sim-title">${escapeHtml(s.title.substring(0, 40))}</span>
              <span class="exam-sim-score" style="color:${s.similarity > 50 ? '#ff6b6b' : s.similarity > 30 ? '#f0a500' : '#4ade80'}">${s.similarity}%</span>
            </div>
          `).join('')}
        </div>
      </div>` : ''}
    </div>
  `;

  // 文章列表错落入场动画（前15个）
  $$('.article-item').forEach((item, i) => {
    if (i < 15) {
      item.style.animationDelay = `${i * 0.05}s`;
    } else {
      item.style.animation = 'none';
      item.style.opacity = '1';
    }
  });
}

let selectedWordIds = new Set();

async function loadVocab() {
  selectedWordIds.clear();
  const words = await api.get('/api/words/vocab?status=learning&limit=100');
  $('#reader').innerHTML = `
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:20px;">
      <button class="toolbar-btn" id="backToReaderBtn" style="display:flex;align-items:center;gap:6px;">← 返回阅读</button>
    </div>
    <h1>生词本 <span style="font-size:13px;color:var(--text-secondary);font-weight:400;">${words.length} 个词</span></h1>
    <div class="batch-add">
      <textarea id="batchWords" placeholder="批量添加，每行一个 / 空格 / 逗号分隔&#10;abandon&#10;benefit&#10;consequence"></textarea>
      <div class="batch-actions">
        <button class="toolbar-btn primary" id="batchAddBtn">批量添加</button>
        <span class="hint">支持换行、空格、逗号分隔</span>
      </div>
    </div>
    <div class="vocab-toolbar" id="vocabToolbar" style="display:none;">
      <label class="select-all-label">
        <input type="checkbox" id="selectAllVocab"> 全选
      </label>
      <span id="selectedCount" style="font-size:12px;color:var(--text-secondary);">已选 0 个</span>
      <div class="vocab-toolbar-spacer"></div>
      <button class="toolbar-btn" id="batchKnownBtn">标记已掌握</button>
      <button class="toolbar-btn danger" id="batchDeleteBtn">删除选中</button>
    </div>
    <div id="vocabList">` +
    (words.length === 0 ? '<p style="color:var(--text-secondary); padding:16px 0;">还没有生词</p>' :
    words.map(w =>
      `<div class="vocab-item" data-id="${w.id}">
        <div class="vocab-item-header">
          <input type="checkbox" class="vocab-checkbox" data-id="${w.id}" ${selectedWordIds.has(w.id) ? 'checked' : ''}>
          <div class="vocab-item-main" style="flex:1;cursor:pointer;">
            <div class="word-row">
              <span class="word">${escapeHtml(w.text)}</span>
              <span class="phonetic">${escapeHtml(w.phonetic || '')}</span>
            </div>
            <div class="meaning">${escapeHtml((w.meaning || '暂无释义').substring(0, 80))}${(w.meaning || '').length > 80 ? '...' : ''}</div>
          </div>
          <button class="vocab-delete-btn" data-id="${w.id}" title="删除">×</button>
        </div>
        <div class="vocab-detail" id="vocabDetail-${w.id}" style="display:none;">
          <div class="vocab-detail-section">
            <div class="vocab-detail-label">释义</div>
            <div class="vocab-detail-meaning">${escapeHtml(w.meaning || '暂无释义')}</div>
          </div>
          <div class="vocab-detail-meta">
            <span>遇见 ${w.encounter_count} 次</span>
            <span>查词 ${w.lookup_count} 次</span>
            <span>${w.level}</span>
            <span>添加于 ${new Date(w.created_at * 1000).toLocaleDateString('zh-CN')}</span>
          </div>
        </div>
        <!-- 悬停详情浮层（和文章卡片一致） -->
        <div class="vocab-detail-popup" data-id="${w.id}">
          <div class="detail-section">
            <div class="detail-label">${escapeHtml(w.text)} ${escapeHtml(w.phonetic || '')}</div>
            <div style="font-size:12px;line-height:1.6;color:var(--text);white-space:pre-wrap;max-height:120px;overflow-y:auto;">${escapeHtml(w.meaning || '暂无释义')}</div>
          </div>
          ${w.exchange ? `<div class="detail-section">
            <div class="detail-label">词形变化</div>
            <div class="detail-tags">${w.exchange.split(/[,;]/).filter(Boolean).slice(0,6).map(e => `<span class="detail-tag">${escapeHtml(e.trim())}</span>`).join('')}</div>
          </div>` : ''}
          <div class="detail-section">
            <div class="detail-label">学习统计</div>
            <div style="display:flex;flex-wrap:wrap;gap:8px;font-size:11px;color:var(--text-secondary);">
              <span>遇见 ${w.encounter_count} 次</span>
              <span>查词 ${w.lookup_count} 次</span>
              <span>正确 ${w.correct_count || 0} 次</span>
              <span>错误 ${w.wrong_count || 0} 次</span>
            </div>
          </div>
          <div class="detail-section" style="margin-bottom:0;">
            <div class="detail-label">记忆状态</div>
            <div style="font-size:11px;color:var(--text-tertiary);">
              ${w.srs_stability ? `强度 ${Math.round(w.srs_stability)} · 间隔 ${Math.round(w.srs_interval || 0)}天` : '未开始复习'}
              ${w.last_test_at ? ` · 上次测试 ${new Date(w.last_test_at * 1000).toLocaleDateString('zh-CN')}` : ''}
            </div>
          </div>
        </div>
      </div>`
    ).join('')) + '</div>';

  // 返回阅读
  const backBtn = $('#backToReaderBtn');
  if (backBtn) {
    backBtn.addEventListener('click', () => {
      $$('.nav-item').forEach(i => i.classList.remove('active'));
      document.querySelector('[data-view="reader"]').classList.add('active');
      if (currentArticle) {
        renderArticle(currentArticle);
      } else {
        $('#reader').innerHTML = `<div class="empty"><h2>ReadLoops</h2><p>点击「生成文章」开始阅读训练</p></div>`;
      }
    });
  }

  // 批量添加
  $('#batchAddBtn').addEventListener('click', async () => {
    const text = $('#batchWords').value.trim();
    if (!text) { toast('请输入生词'); return; }
    const ws = text.split(/[\n,，;；\s]+/).filter(w => w.trim() && /[a-zA-Z]/.test(w));
    if (!ws.length) { toast('没有有效的单词'); return; }
    const result = await api.post('/api/words/batch-add', { words: ws });
    toast(`已添加 ${result.added} 个生词`);
    $('#batchWords').value = '';
    loadVocab();
  });

  // 词条点击展开/折叠
  $$('.vocab-item-main').forEach(el => {
    el.addEventListener('click', () => {
      const item = el.closest('.vocab-item');
      const id = item.dataset.id;
      const detail = document.getElementById(`vocabDetail-${id}`);
      if (detail.style.display === 'none') {
        detail.style.display = 'block';
      } else {
        detail.style.display = 'none';
      }
    });
  });

  // 单个删除
  $$('.vocab-delete-btn').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const id = btn.dataset.id;
      if (!await confirmDialog('确定删除这个词？', { danger: true })) return;
      await api.delete(`/api/words/${id}`);
      toast('已删除');
      loadVocab();
    });
  });

  // 多选
  $$('.vocab-checkbox').forEach(cb => {
    cb.addEventListener('click', (e) => e.stopPropagation());
    cb.addEventListener('change', () => {
      const id = parseInt(cb.dataset.id);
      if (cb.checked) selectedWordIds.add(id);
      else selectedWordIds.delete(id);
      updateVocabToolbar();
    });
  });

  // 全选
  const selectAll = $('#selectAllVocab');
  if (selectAll) {
    selectAll.addEventListener('change', () => {
      $$('.vocab-checkbox').forEach(cb => {
        cb.checked = selectAll.checked;
        const id = parseInt(cb.dataset.id);
        if (selectAll.checked) selectedWordIds.add(id);
        else selectedWordIds.delete(id);
      });
      updateVocabToolbar();
    });
  }

  // 批量删除
  const batchDel = $('#batchDeleteBtn');
  if (batchDel) {
    batchDel.addEventListener('click', async () => {
      if (selectedWordIds.size === 0) { toast('请先选择'); return; }
      if (!await confirmDialog(`确定删除选中的 ${selectedWordIds.size} 个词？`, { danger: true })) return;
      await api.post('/api/words/batch-delete', { word_ids: [...selectedWordIds] });
      toast(`已删除 ${selectedWordIds.size} 个词`);
      loadVocab();
    });
  }

  // 批量标记已掌握
  const batchKnown = $('#batchKnownBtn');
  if (batchKnown) {
    batchKnown.addEventListener('click', async () => {
      if (selectedWordIds.size === 0) { toast('请先选择'); return; }
      await api.post('/api/words/batch-status', { word_ids: [...selectedWordIds], status: 'known' });
      toast(`已标记 ${selectedWordIds.size} 个词为已掌握`);
      loadVocab();
    });
  }

  // 生词本词条错落入场动画（前20个，避免大量动画卡顿）
  $$('.vocab-item').forEach((item, i) => {
    if (i < 20) {
      item.style.animationDelay = `${i * 0.04}s`;
    } else {
      item.style.animation = 'none';
      item.style.opacity = '1';
    }
  });
}

function updateVocabToolbar() {
  const toolbar = $('#vocabToolbar');
  const count = selectedWordIds.size;
  if (count > 0) {
    toolbar.style.display = 'flex';
    $('#selectedCount').textContent = `已选 ${count} 个`;
  } else {
    toolbar.style.display = 'none';
  }
  const selectAll = $('#selectAllVocab');
  if (selectAll) {
    const total = $$('.vocab-checkbox').length;
    selectAll.checked = count === total && total > 0;
  }
}

async function loadStats() {
  const [s, activity] = await Promise.all([
    api.get('/api/stats/overview'),
    api.get('/api/stats/activity?weeks=26'),
  ]);
  const mins = Math.floor(s.total_reading_seconds / 60);
  const hours = Math.floor(mins / 60);
  const timeStr = hours > 0 ? `${hours}小时${mins % 60}分` : `${mins}分钟`;

  // 渲染热力图
  const heatmapHTML = activity.days.map(d => {
    const mins = Math.floor(d.reading_seconds / 60);
    const tooltip = `${d.date}\n${d.articles}篇文章 · ${mins}分钟 · ${d.lookups}次查词`;
    return `<div class="heatmap-cell level-${d.level}" data-tooltip="${tooltip}" data-date="${d.date}"></div>`;
  }).join('');

  $('#reader').innerHTML = `
    <h1>学习统计</h1>

    <!-- 概览卡片 -->
    <div class="stats-grid" style="grid-template-columns: repeat(4, 1fr);">
      <div class="stat-card">
        <div class="num">${s.articles_read}</div>
        <div class="label">已读文章</div>
      </div>
      <div class="stat-card">
        <div class="num">${s.total_words.toLocaleString()}</div>
        <div class="label">总阅读词数</div>
      </div>
      <div class="stat-card">
        <div class="num">${timeStr}</div>
        <div class="label">阅读时长</div>
      </div>
      <div class="stat-card">
        <div class="num">${s.streak_days}</div>
        <div class="label">连续学习天数</div>
      </div>
      <div class="stat-card">
        <div class="num">${s.vocab_estimate.toLocaleString()}</div>
        <div class="label">估算词汇量</div>
      </div>
      <div class="stat-card">
        <div class="num">${s.test_accuracy}%</div>
        <div class="label">测试正确率</div>
      </div>
      <div class="stat-card">
        <div class="num">${s.total_lookups}</div>
        <div class="label">查词次数</div>
      </div>
      <div class="stat-card">
        <div class="num">${s.srs.due_today}</div>
        <div class="label">今日待复习</div>
      </div>
    </div>

    <!-- 阅读热力图 -->
    <div class="stats-section">
      <h2 class="stats-section-title">阅读活动 · 近26周（活跃 ${activity.active_days} 天）</h2>
      <div class="heatmap-container">
        <div class="heatmap">${heatmapHTML}</div>
      </div>
      <div class="heatmap-legend">
        <span>少</span>
        <div class="heatmap-cell"></div>
        <div class="heatmap-cell level-1"></div>
        <div class="heatmap-cell level-2"></div>
        <div class="heatmap-cell level-3"></div>
        <div class="heatmap-cell level-4"></div>
        <span>多</span>
      </div>
    </div>

    <!-- 词汇状态 -->
    <div class="stats-section">
      <h2 class="stats-section-title">词汇状态</h2>
      <div class="vocab-status-bar">
        <div class="vocab-status-item" style="flex:${s.words_known}">
          <span class="vocab-status-label">已掌握 ${s.words_known}</span>
        </div>
        <div class="vocab-status-item learning" style="flex:${s.words_learning}">
          <span class="vocab-status-label">学习中 ${s.words_learning}</span>
        </div>
        <div class="vocab-status-item target" style="flex:${s.words_target}">
          <span class="vocab-status-label">测验词 ${s.words_target}</span>
        </div>
      </div>
    </div>

    <!-- FSRS 记忆统计 -->
    <div class="stats-section">
      <h2 class="stats-section-title">FSRS 记忆系统</h2>
      <div class="stats-grid" style="grid-template-columns: repeat(3, 1fr);">
        <div class="stat-card">
          <div class="num">${s.srs.total}</div>
          <div class="label">纳入复习词数</div>
        </div>
        <div class="stat-card">
          <div class="num">${s.srs.avg_stability}</div>
          <div class="label">平均记忆稳定性</div>
        </div>
        <div class="stat-card">
          <div class="num">${s.srs.due_today}</div>
          <div class="label">今日到期复习</div>
        </div>
      </div>
    </div>

    <!-- 查词热词 -->
    ${s.hot_words && s.hot_words.length > 0 ? `
    <div class="stats-section">
      <h2 class="stats-section-title">查词热词 TOP ${s.hot_words.length}</h2>
      <div class="hot-words-list">
        ${s.hot_words.map((w, i) => `
          <div class="hot-word-item">
            <span class="hot-word-rank">${i + 1}</span>
            <span class="hot-word-text">${escapeHtml(w.text)}</span>
            <div class="hot-word-bar-bg">
              <div class="hot-word-bar" style="width:${(w.count / s.hot_words[0].count * 100)}%"></div>
            </div>
            <span class="hot-word-count">${w.count}次</span>
          </div>
        `).join('')}
      </div>
    </div>` : ''}
  `;

  // 热力图 tooltip
  initHeatmapTooltip();
}

// 热力图悬停提示
function initHeatmapTooltip() {
  let tooltip = document.getElementById('heatmapTooltip');
  if (!tooltip) {
    tooltip = document.createElement('div');
    tooltip.id = 'heatmapTooltip';
    tooltip.className = 'heatmap-tooltip';
    document.body.appendChild(tooltip);
  }
  $$('.heatmap-cell').forEach(cell => {
    cell.addEventListener('mouseenter', (e) => {
      const data = cell.dataset.tooltip.split('\n');
      tooltip.innerHTML = `<div class="tt-date">${data[0]}</div><div class="tt-row">${data[1]}</div>`;
      tooltip.classList.add('show');
    });
    cell.addEventListener('mousemove', (e) => {
      tooltip.style.left = (e.clientX + 12) + 'px';
      tooltip.style.top = (e.clientY - 10) + 'px';
    });
    cell.addEventListener('mouseleave', () => {
      tooltip.classList.remove('show');
    });
  });
}

// 单词测试
let testQuestions = [];
let testCurrent = 0;
let testScore = 0;
let testAnswered = false;
let testConfig = { type: 'vocab', count: 20, difficulty: 'mixed' };
let testWrongAnswers = [];

async function loadTest() {
  testQuestions = [];
  testCurrent = 0;
  testScore = 0;
  testAnswered = false;
  testWrongAnswers = [];
  // 获取 FSRS 统计
  try {
    const srsStats = await api.get('/api/words/srs-stats');
    window._srsStats = srsStats;
  } catch(e) { window._srsStats = null; }
  renderTestHome();
}

function renderTestHome() {
  const srs = window._srsStats;
  $('#reader').innerHTML = `
    <h1>单词测试</h1>
    <div class="test-home">
      ${srs && srs.due_today > 0 ? `
      <div class="srs-review-banner" onclick="startSrsReview()">
        <div class="srs-review-info">
          <span class="srs-review-count">${srs.due_today}</span>
          <span class="srs-review-label">个词今日待复习</span>
        </div>
        <button class="toolbar-btn primary">开始复习</button>
      </div>` : ''}
      <div class="test-section">
        <div class="test-section-title">测试类型</div>
        <div class="test-type-grid">
          <div class="test-type-card ${testConfig.type === 'vocab' ? 'active' : ''}" data-type="vocab">
            <div class="test-type-name">词汇量测试</div>
            <div class="test-type-desc">从全部词库随机抽题</div>
          </div>
          <div class="test-type-card ${testConfig.type === 'learning' ? 'active' : ''}" data-type="learning">
            <div class="test-type-name">生词复习</div>
            <div class="test-type-desc">只考生词本中的词</div>
          </div>
          <div class="test-type-card ${testConfig.type === 'target' ? 'active' : ''}" data-type="target">
            <div class="test-type-name">测验词复习</div>
            <div class="test-type-desc">只考查过2次的目标词</div>
          </div>
          <div class="test-type-card disabled" data-type="grammar">
            <div class="test-type-name">语法测试</div>
            <div class="test-type-desc">即将上线</div>
          </div>
        </div>
      </div>

      <div class="test-section">
        <div class="test-section-title">题量</div>
        <div class="test-option-group">
          ${[10, 20, 50].map(n =>
            `<button class="test-option-btn ${testConfig.count === n ? 'active' : ''}" data-count="${n}">${n} 题</button>`
          ).join('')}
        </div>
      </div>

      <div class="test-section">
        <div class="test-section-title">难度</div>
        <div class="test-option-group">
          ${[
            {v: 'easy', l: '简单（高频词）'},
            {v: 'medium', l: '中等（中频词）'},
            {v: 'hard', l: '困难（低频词）'},
            {v: 'mixed', l: '混合'}
          ].map(d =>
            `<button class="test-option-btn ${testConfig.difficulty === d.v ? 'active' : ''}" data-difficulty="${d.v}">${d.l}</button>`
          ).join('')}
        </div>
      </div>

      <button class="toolbar-btn primary test-start-btn" id="startTestBtn">开始测试</button>
    </div>`;

  // 测试类型选择
  $$('.test-type-card').forEach(card => {
    if (card.classList.contains('disabled')) return;
    card.addEventListener('click', () => {
      testConfig.type = card.dataset.type;
      $$('.test-type-card').forEach(c => c.classList.remove('active'));
      card.classList.add('active');
    });
  });

  // 题量选择
  $$('[data-count]').forEach(btn => {
    btn.addEventListener('click', () => {
      testConfig.count = parseInt(btn.dataset.count);
      $$('[data-count]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
    });
  });

  // 难度选择
  $$('[data-difficulty]').forEach(btn => {
    btn.addEventListener('click', () => {
      testConfig.difficulty = btn.dataset.difficulty;
      $$('[data-difficulty]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
    });
  });

  $('#startTestBtn').addEventListener('click', startTest);
}

async function startSrsReview() {
  // 开始 FSRS 今日复习测试
  try {
    const dueWords = await api.get('/api/words/review-due?limit=20');
    if (dueWords.length === 0) {
      toast('今天没有待复习的词');
      return;
    }
    // 创建测试记录
    let testId = null;
    try {
      const testRes = await api.post('/api/words/test/start', {});
      testId = testRes.test_id;
    } catch(e) { console.log('创建测试记录失败', e); }

    // 生成题目（从到期词中抽）
    testQuestions = await api.get(
      `/api/words/test/generate?count=${Math.min(20, dueWords.length)}&difficulty=mixed&source=vocab`
    );
    if (testQuestions.length === 0) {
      toast('没有足够的题目');
      return;
    }
    window._currentTestId = testId;
    testCurrent = 0;
    testScore = 0;
    testAnswered = false;
    testWrongAnswers = [];
    renderTestQuestion();
    toast(`开始复习 ${dueWords.length} 个词`);
  } catch(err) {
    console.error(err);
    toast('开始复习失败');
  }
}

async function startTest() {
  const sourceMap = { vocab: 'adaptive', learning: 'vocab', target: 'target' };
  const source = sourceMap[testConfig.type] || 'adaptive';
  try {
    // 创建测试记录
    let testId = null;
    try {
      const testRes = await api.post('/api/words/test/start', {});
      testId = testRes.test_id;
    } catch(e) { console.log('创建测试记录失败', e); }

    testQuestions = await api.get(
      `/api/words/test/generate?count=${testConfig.count}&difficulty=${testConfig.difficulty}&source=${source}`
    );
    if (testQuestions.length === 0) {
      toast('没有足够的题目，请换个范围或难度');
      return;
    }
    testCurrent = 0;
    testScore = 0;
    testWrongAnswers = [];
    window.currentTestId = testId;
    renderTestQuestion();
  } catch (e) {
    $('#reader').innerHTML = '<h1>单词测试</h1><p style="color:var(--text-secondary);">生成题目失败，请重试</p>';
  }
}

function renderTestQuestion() {
  if (testCurrent >= testQuestions.length) {
    renderTestResult();
    return;
  }
  testAnswered = false;
  const q = testQuestions[testCurrent];
  const startTime = Date.now();

  // 题型显示
  let questionDisplay = '';
  if (q.type === 'word_to_meaning') {
    questionDisplay = `<div class="test-word">${escapeHtml(q.word)}</div>`;
  } else if (q.type === 'meaning_to_word') {
    questionDisplay = `<div class="test-question-text">${escapeHtml(q.question)}</div>`;
  } else if (q.type === 'word_form') {
    questionDisplay = `<div class="test-question-text">${escapeHtml(q.question)}</div>`;
  } else {
    questionDisplay = `<div class="test-word">${escapeHtml(q.word)}</div>`;
  }

  $('#reader').innerHTML = `
    <div class="test-header">
      <span>第 ${testCurrent + 1} / ${testQuestions.length} 题</span>
      <span>得分：${testScore}</span>
    </div>
    <div class="test-progress"><div class="test-progress-bar" style="width:${(testCurrent / testQuestions.length) * 100}%"></div></div>
    ${questionDisplay}
    <div class="test-options">
      ${q.options.map((opt, i) =>
        `<button class="test-option" data-index="${i}">${String.fromCharCode(65 + i)}. ${escapeHtml(opt)}</button>`
      ).join('')}
    </div>`;
  $$('.test-option').forEach(btn => {
    btn.addEventListener('click', () => {
      if (testAnswered) return;
      testAnswered = true;
      const selected = btn.dataset.index;
      const correct = q.options.indexOf(q.answer);
      const isCorrect = parseInt(selected) === correct;
      const reactionTime = Date.now() - startTime;

      if (isCorrect) {
        testScore++;
        btn.classList.add('correct');
      } else {
        btn.classList.add('wrong');
        $$('.test-option')[correct].classList.add('correct');
        testWrongAnswers.push({ word: q.word, answer: q.answer, selected: q.options[selected] });
      }

      // 提交结果到后端
      if (window.currentTestId && q.word_id) {
        api.post(`/api/words/test/${window.currentTestId}/submit?word_id=${q.word_id}&is_correct=${isCorrect}&reaction_time=${reactionTime}`, {})
          .catch(e => console.log('提交结果失败', e));
      }

      setTimeout(() => { testCurrent++; renderTestQuestion(); }, 1000);
    });
  });
}

function renderTestResult() {
  // 记录今天已测试
  localStorage.setItem('readloops_last_test_date', new Date().toDateString());

  const pct = Math.round((testScore / testQuestions.length) * 100);
  let level = '', levelColor = '';
  if (pct >= 90) { level = '优秀'; levelColor = '#4ade80'; }
  else if (pct >= 70) { level = '良好'; levelColor = '#60a5fa'; }
  else if (pct >= 50) { level = '及格'; levelColor = '#fbbf24'; }
  else { level = '需加强'; levelColor = '#ff6b6b'; }

  const typeNames = { vocab: '词汇量测试', learning: '生词复习', target: '测验词复习' };
  const diffNames = { easy: '简单', medium: '中等', hard: '困难', mixed: '混合' };

  $('#reader').innerHTML = `
    <h1>测试结果</h1>
    <div class="test-result-card">
      <div class="test-score" style="color:${levelColor}">${testScore} / ${testQuestions.length}</div>
      <div class="test-percent">${pct}%</div>
      <div class="test-level" style="color:${levelColor}">${level}</div>
      <div class="test-meta">
        <span>${typeNames[testConfig.type] || testConfig.type}</span>
        <span>${diffNames[testConfig.difficulty]}</span>
        <span>${testQuestions.length} 题</span>
      </div>
    </div>

    ${testWrongAnswers.length > 0 ? `
    <div class="test-wrong-section">
      <div class="test-section-title">错题回顾（${testWrongAnswers.length} 题）</div>
      <div class="test-wrong-list">
        ${testWrongAnswers.map(w => `
          <div class="test-wrong-item">
            <div class="test-wrong-word">${escapeHtml(w.word)}</div>
            <div class="test-wrong-correct">正确：${escapeHtml(w.answer)}</div>
            <div class="test-wrong-selected">你的选择：${escapeHtml(w.selected)}</div>
          </div>
        `).join('')}
      </div>
    </div>` : ''}

    <div style="display:flex;gap:10px;margin-top:24px;flex-wrap:wrap;">
      <button class="toolbar-btn primary" id="retryTestBtn">再测一次</button>
      <button class="toolbar-btn" id="backToTestHomeBtn">返回设置</button>
      <button class="toolbar-btn" id="backToReaderBtn">返回阅读</button>
    </div>`;

  $('#retryTestBtn').addEventListener('click', startTest);
  $('#backToTestHomeBtn').addEventListener('click', renderTestHome);
  $('#backToReaderBtn').addEventListener('click', () => {
    document.querySelector('[data-view="reader"]').click();
  });
}

window.addEventListener('beforeunload', () => {
  if (currentArticle && timerSeconds > 0) {
    navigator.sendBeacon('/api/reading/session', JSON.stringify({
      article_id: currentArticle.id, duration_seconds: timerSeconds,
      lookups: lookupsInSession, highlights: highlightsInSession
    }));
  }
});

// 页面加载时显示最新文章
async function loadLatestArticle() {
  try {
    const list = await api.get('/api/articles/?limit=1');
    if (list && list.length > 0) {
      const article = await api.get(`/api/articles/${list[0].id}`);
      currentArticle = article;
      renderArticle(article);
      $('#articleMeta').textContent = `${article.word_count} 词 · ${article.new_word_count} 生词`;
    }
  } catch(e) { console.log('加载最新文章失败', e); }
}
loadLatestArticle();

// 右侧查词历史滑出面板
const rightPanel = $('#rightPanel');
const rightPanelTrigger = $('#rightPanelTrigger');
let panelHoverTimer = null;
let panelPinned = false; // 工具栏按钮打开时固定显示

// 鼠标移入右侧边缘触发（带防抖，避免快速划过误触）
rightPanelTrigger.addEventListener('mouseenter', () => {
  clearTimeout(panelHoverTimer);
  panelHoverTimer = setTimeout(() => {
    if (!panelPinned) {
      rightPanel.classList.add('show');
      loadLookupHistory();
    }
  }, 120);
});

rightPanelTrigger.addEventListener('mouseleave', () => {
  clearTimeout(panelHoverTimer);
});

// 鼠标离开面板时收起（除非被固定）
rightPanel.addEventListener('mouseleave', () => {
  if (!panelPinned) {
    rightPanel.classList.remove('show');
  }
});

// 工具栏按钮切换（点击展开，离开侧页自动收回，和边缘触发逻辑一致）
$('#lookupHistoryBtn').addEventListener('click', () => {
  rightPanel.classList.add('show');
  loadLookupHistory();
});

async function loadLookupHistory() {
  try {
    const history = await api.get('/api/reading/lookups?limit=50');
    $('#lookupCount').textContent = history.length;
    if (history.length === 0) {
      $('#lookupHistoryList').innerHTML = '<div class="right-panel-empty">还没有查词记录<br>选中文字即可查词</div>';
      return;
    }
    $('#lookupHistoryList').innerHTML = history.map(item => `
      <div class="lookup-history-item" data-word="${escapeHtml(item.text)}" data-id="${item.word_id}">
        <div class="word-row">
          <span class="word">${escapeHtml(item.text)}</span>
          <span class="phonetic">${escapeHtml(item.phonetic)}</span>
        </div>
        <div class="meaning">${escapeHtml(item.meaning || '暂无释义')}</div>
        <div class="meta">
          <span>查${item.lookup_count}次 · ${new Date(item.last_lookup * 1000).toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit'})}</span>
          <button class="add-btn" data-word="${escapeHtml(item.text)}" data-id="${item.word_id}">+ 生词本</button>
        </div>
      </div>
    `).join('');
    // 绑定加入生词本按钮
    $$('.lookup-history-item .add-btn').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const word = btn.dataset.word;
        await api.post('/api/words/add', { word });
        toast(`已添加「${word}」到生词本`);
        btn.textContent = '已添加';
        btn.disabled = true;
      });
    });
  } catch(e) { console.log('加载查词历史失败', e); }
}

// 查词后刷新历史（在原有的查词成功后调用）
const originalShowLookupPanel = showLookupPanel;
// 在查词成功后刷新历史（通过 MutationObserver 或直接在 lookup 成功后调用）
// 简化：每次面板显示后延迟刷新
const observer = new MutationObserver(() => {
  if (lookupPanel.classList.contains('show')) {
    setTimeout(loadLookupHistory, 500);
  }
});
observer.observe(lookupPanel, { attributes: true, attributeFilter: ['class'] });

// === 每日测试弹窗 ===
function checkDailyTestPrompt() {
  const today = new Date().toDateString();
  const lastPrompt = localStorage.getItem('readloops_last_test_prompt');
  const lastTest = localStorage.getItem('readloops_last_test_date');

  // 今天已经测试过，不弹窗
  if (lastTest === today) return;
  // 今天已经提示过，不重复弹窗
  if (lastPrompt === today) return;

  localStorage.setItem('readloops_last_test_prompt', today);
  showDailyTestModal();
}

function showDailyTestModal() {
  const modal = document.createElement('div');
  modal.className = 'modal-overlay';
  modal.id = 'dailyTestModal';
  modal.innerHTML = `
    <div class="modal-content daily-test-modal">
      <div class="modal-title">每日词汇测试</div>
      <div class="modal-body">
        <p>建议每天进行一次词汇测试，巩固阅读中遇到的生词。</p>
        <p style="color:var(--text-secondary);font-size:12px;margin-top:8px;">
          测试题目基于你的阅读数据智能生成，优先出你不会的词。
        </p>
      </div>
      <div class="modal-actions">
        <button class="toolbar-btn" id="skipTestBtn">稍后</button>
        <button class="toolbar-btn primary" id="startTestBtn">开始测试</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  modal.style.display = 'flex';

  document.getElementById('startTestBtn').addEventListener('click', () => {
    modal.remove();
    // 切换到测试页面
    $$('.nav-item').forEach(i => i.classList.remove('active'));
    document.querySelector('[data-view="test"]').classList.add('active');
    loadTest();
  });

  document.getElementById('skipTestBtn').addEventListener('click', () => {
    modal.remove();
  });

  modal.addEventListener('click', (e) => {
    if (e.target === modal) modal.remove();
  });
}

// 页面加载后延迟检查弹窗
setTimeout(checkDailyTestPrompt, 1500);
