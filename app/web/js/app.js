// ReadLoops 阅读器
const $ = (s) => document.querySelector(s);
// 返回真数组（而不是 NodeList）：NodeList 没有 filter/map 等方法，
// 图谱页需要按类型过滤节点，用数组更省事；forEach 用法完全兼容。
const $$ = (s) => Array.from(document.querySelectorAll(s));

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
  // 切换瞬间临时开启全局颜色过渡，让四个主题之间平滑渐变而不是硬切。
  // 过渡只在这 420ms 内生效，不会拖慢平时的交互动画。
  document.body.classList.add('theme-transition');
  document.documentElement.setAttribute('data-theme', e.target.value);
  localStorage.setItem('yuedu-theme', e.target.value);
  clearTimeout(window.__themeTransitionTimer);
  window.__themeTransitionTimer = setTimeout(() => {
    document.body.classList.remove('theme-transition');
  }, 420);
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
  // 专注模式只服务于「沉浸阅读」：非阅读页一律不允许进入。
  // 这是兜底防线——无论从按钮、快捷键还是将来的新入口触发，都拦得住。
  const view = document.querySelector('.nav-item.active')?.dataset.view;
  if (!focusMode && view && view !== 'reader') {
    toast('专注模式仅在阅读页可用');
    return;
  }
  focusMode = !focusMode;
  document.querySelector('.app').classList.toggle('focus-mode', focusMode);
  // body 同步切类：驱动氛围光层上移 / 回位（过渡与水纹涟漪对齐）
  document.body.classList.toggle('focus-mode', focusMode);
  if (focusMode) {
    focusStartTime = Date.now();
    $('#focusBtn').textContent = '退出专注';
    $('#focusBtn').classList.add('active');
    // 进入专注 = 开始沉浸阅读：有文章且计时未跑时同步开始计时
    if (currentArticle && !timerRunning) startTimer();
    toast('已进入专注模式');
    // 触发水纹扩散引导
    setTimeout(triggerRipple, 300);
  } else {
    const duration = Math.round((Date.now() - focusStartTime) / 1000);
    focusTotalTime += duration;
    $('#focusBtn').textContent = '专注';
    $('#focusBtn').classList.remove('active');
    // 退出专注 = 暂停阅读：同步暂停计时（再次进入会从当前读数继续）
    if (timerRunning) pauseTimer();
    // 已非专注，计时按钮回到「开始（即进入专注）」语义，而不是「继续」
    $('#timerBtn').textContent = '开始 [空格]';
    const mins = Math.floor(duration / 60);
    const secs = duration % 60;
    toast(`本次专注 ${mins}分${secs}秒`);
  }
}

// 文章互动自动开启专注模式
function tryAutoFocus() {
  // 只在阅读页自动进入专注。
  // 生词本 / 历史文章 / 统计页的内容同样渲染在 #readerContainer 里，
  // 在这些页面点一下也会走到这里；若不拦住，autoFocusTriggered 会被
  // 提前置为 true，回到阅读页后自动专注就再也触发不了了。
  const view = document.querySelector('.nav-item.active')?.dataset.view;
  if (view !== 'reader') return;
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
  // F 切换专注（仅在阅读页、不在输入框中时）
  if (e.key === 'f' && !focusMode && !['INPUT','TEXTAREA'].includes(e.target.tagName)) {
    const view = document.querySelector('.nav-item.active')?.dataset.view;
    if (view !== 'reader') return;   // 非阅读页不响应，也不吞掉按键
    e.preventDefault();
    toggleFocusMode();
    return;
  }
});

// 计时器
function formatTime(s) {
  const m = Math.floor(s / 60).toString().padStart(2, '0');
  const sec = (s % 60).toString().padStart(2, '0');
  return `${m}:${sec}`;
}
function startTimer() {
  // 计时器与专注模式一体：非专注状态下一律不计时（根防线，任何入口都绕不过）
  if (!focusMode) return;
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
// 计时与专注一体：非专注时「开始计时」即进入专注模式；已在专注中则只做暂停 / 继续
function toggleTimerWithFocus() {
  if (!focusMode) {
    const view = document.querySelector('.nav-item.active')?.dataset.view;
    if (view !== 'reader' || !currentArticle) return; // 非阅读页 / 还没文章：不启动
    toggleFocusMode(); // 进入专注，其进入分支会负责 startTimer
  } else {
    timerRunning ? pauseTimer() : startTimer();
  }
}
$('#timerBtn').addEventListener('click', toggleTimerWithFocus);

document.addEventListener('keydown', (e) => {
  if (e.code === 'Space' && !['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName)) {
    e.preventDefault();
    toggleTimerWithFocus();
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

/**
 * 给成组出现的元素设置错峰延迟，避免它们「一起蹦出来」。
 * 延迟随序号递增，但有上限，防止长列表最后一项等太久。
 */
function staggerIn(selector, step = 0.045, max = 0.45) {
  $$(selector).forEach((el, i) => {
    el.style.animationDelay = Math.min(i * step, max) + 's';
  });
}

// 划词查词
const lookupPanel = $('#lookupPanel');
let currentSelection = '';

document.addEventListener('mouseup', (e) => {
  if (lookupPanel.contains(e.target)) return;
  const sel = window.getSelection();
  const text = sel.toString().trim();
  // 上限 280：整句翻译需要容纳 1~2 个完整句子（原 60 稍长一点的句子直接不弹面板）
  if (!text || text.length > 280 || !/[a-zA-Z]/.test(text)) { hideLookupPanel(); return; }
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
  // 计时由「划词自动进入专注」统一带动（mouseup → tryAutoFocus → 进专注即计时），
  // 这里不再单独 startTimer，避免出现非专注计时

  // 选中多个单词 → 翻译整句；单个单词 → 查词典
  if (/\s/.test(word.trim())) {
    lookupPanel.classList.add('sentence-mode');
    lookupPanel.dataset.wordId = '';
    requestSentenceTranslate(word);
    return;
  }
  lookupPanel.classList.remove('sentence-mode');

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
// 整句翻译：独立函数，失败时在面板内给出「点击重试」而不是一句死掉的「翻译失败」
async function requestSentenceTranslate(word) {
  const meaning = $('#lpMeaning');
  meaning.textContent = '翻译中...';
  try {
    const res = await api.post('/api/words/translate', { text: word });
    if (res && res.ok && res.translation) {
      meaning.textContent = res.translation;
      return;
    }
    renderTranslateRetry(word, res && res.error);
  } catch (err) {
    renderTranslateRetry(word, String(err));
  }
}
function renderTranslateRetry(word, reason) {
  if (reason) console.warn('整句翻译失败:', reason);
  const meaning = $('#lpMeaning');
  meaning.innerHTML = '';
  const hint = document.createElement('div');
  hint.className = 'translate-hint';
  hint.textContent = '翻译失败（网络或 AI 服务波动）';
  const btn = document.createElement('button');
  btn.className = 'translate-retry-btn';
  btn.textContent = '点击重试';
  btn.addEventListener('click', () => requestSentenceTranslate(word));
  meaning.append(hint, btn);
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
async function switchPage(renderFn) {
  if (isSwitching) return;
  isSwitching = true;
  // 切换页面时停止打字机
  stopTypewriter();
  const reader = $('#reader');
  // 1. 淡出（只用opacity，减少重绘）
  reader.classList.add('page-fading');
  setTimeout(async () => {
    // 2. 替换内容 —— 必须 await！
    //    renderFn 里多是异步函数（loadStats / loadVocab / loadArticlesList…），
    //    它们要先拿到接口数据才写 innerHTML。不等它完成就淡入的话，
    //    会先把「上一页的旧内容」淡进来，等数据回来再被换掉 —— 这就是残影。
    try {
      await renderFn();
    } catch (err) {
      console.error('页面渲染失败:', err);
    }
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
    // 底部计时栏也只属于阅读页 —— 出现在统计 / 生词本里很突兀。
    const timerBar = document.querySelector('.timer-bar');
    if (timerBar) timerBar.style.display = (activeView === 'reader') ? '' : 'none';
    // 3. 淡入
    reader.classList.remove('page-fading');
    reader.classList.remove('page-enter');
    void reader.offsetWidth; // 强制重排
    reader.classList.add('page-enter');
    $('#readerContainer').scrollTop = 0;
    setTimeout(() => {
      reader.classList.remove('page-enter');
      isSwitching = false;
    }, 520);
  }, 100);
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
          $('#reader').innerHTML = `<div class="empty">
        <h2>ReadLoops</h2>
        <p>点击「生成文章」开始阅读训练</p>
        <div class="empty-hints">
          <span><kbd>空格</kbd>开始专注计时 · 暂停继续</span>
          <span><kbd>选中</kbd>查词 · 选整句翻译</span>
          <span><kbd>F</kbd>切换专注模式</span>
        </div>
      </div>`;
        }
      } else if (view === 'articles') loadArticlesList();
      else if (view === 'vocab') loadVocab();
      else if (view === 'test') loadTest();
      else if (view === 'stats') loadStats();
      else if (view === 'library') loadLibrary();
      else if (view === 'materials') loadMaterials();
      else if (view === 'graph') loadGraph();
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

  // 错峰浮现，避免几十条一起涌上来
  staggerIn('.article-item');

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
      <textarea id="batchWords" placeholder="粘贴任意词汇材料：每行一个、带释义、带音标、编号列表都可以&#10;abandon - 放弃，遗弃&#10;benefit n. 好处；v. 受益&#10;consequence /ˈkɒnsɪkwəns/ 后果"></textarea>
      <div class="batch-actions">
        <button class="toolbar-btn primary" id="batchAddBtn">AI 识别并预览</button>
        <button class="toolbar-btn" id="batchQuickAddBtn">直接添加</button>
        <span class="hint">AI 会识别词条、释义与等级；确认前可编辑</span>
      </div>
      <div id="batchPreview" class="batch-preview" style="display:none;"></div>
    </div>
    <div class="vocab-toolbar" id="vocabToolbar">
      <label class="select-all-label">
        <input type="checkbox" id="selectAllVocab"> 全选
      </label>
      <span id="selectedCount" style="font-size:12px;color:var(--text-secondary);">勾选单词后可批量操作</span>
      <div class="vocab-toolbar-spacer"></div>
      <button class="toolbar-btn" id="batchKnownBtn" disabled>标记已掌握</button>
      <button class="toolbar-btn danger" id="batchDeleteBtn" disabled>删除选中</button>
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

  // 错峰浮现
  staggerIn('.vocab-item');

  // 返回阅读
  const backBtn = $('#backToReaderBtn');
  if (backBtn) {
    backBtn.addEventListener('click', () => {
      $$('.nav-item').forEach(i => i.classList.remove('active'));
      document.querySelector('[data-view="reader"]').classList.add('active');
      if (currentArticle) {
        renderArticle(currentArticle);
      } else {
        $('#reader').innerHTML = `<div class="empty">
        <h2>ReadLoops</h2>
        <p>点击「生成文章」开始阅读训练</p>
        <div class="empty-hints">
          <span><kbd>空格</kbd>开始专注计时 · 暂停继续</span>
          <span><kbd>选中</kbd>查词 · 选整句翻译</span>
          <span><kbd>F</kbd>切换专注模式</span>
        </div>
      </div>`;
      }
    });
  }

  // 批量添加：默认走「识别 → 可编辑预览 → 确认」，保留直接添加作为无等待快捷方式。
  $('#batchAddBtn').addEventListener('click', async () => {
    const text = $('#batchWords').value.trim();
    if (!text) { toast('请输入生词'); return; }
    const btn = $('#batchAddBtn');
    btn.disabled = true;
    btn.textContent = '识别中…';
    try {
      const result = await api.post('/api/words/batch-recognize', { text });
      renderBatchPreview(result);
    } catch (e) {
      toast(e.message || '识别失败');
    } finally {
      btn.disabled = false;
      btn.textContent = 'AI 识别并预览';
    }
  });

  $('#batchQuickAddBtn').addEventListener('click', async () => {
    const text = $('#batchWords').value.trim();
    if (!text) { toast('请输入生词'); return; }
    // 直接模式只取纯英文 token，避免把中文释义和词性缩写带入库。
    const ws = [...new Set((text.match(/\b[a-zA-Z][a-zA-Z'-]*\b/g) || [])
      .map(w => w.toLowerCase())
      .filter(w => !['n','v','vi','vt','adj','adv','prep','conj','pron','num','art'].includes(w)))];
    if (!ws.length) { toast('没有有效的英文词条'); return; }
    const result = await api.post('/api/words/batch-add', { words: ws });
    toast(batchAddSummary(result));
    $('#batchWords').value = '';
    loadVocab();
  });

  function renderBatchPreview(result) {
    const box = $('#batchPreview');
    const items = result.items || [];
    if (!items.length) {
      box.style.display = 'block';
      box.innerHTML = `<div class="batch-preview-empty">没有识别到有效英文词条。可尝试每行一个词，或用「单词 - 释义」格式。</div>`;
      return;
    }
    box.style.display = 'block';
    box.innerHTML = `
      <div class="batch-preview-head">
        <span>识别到 <b>${items.length}</b> 个词条</span>
        <span class="batch-preview-mode">${result.mode === 'ai' ? 'AI 识别' : '本地解析'}</span>
      </div>
      ${result.warning ? `<div class="batch-preview-warning">${escapeHtml(result.warning)}</div>` : ''}
      <div class="batch-preview-list">
        ${items.map((it, idx) => `
          <div class="batch-preview-item" data-index="${idx}">
            <input type="checkbox" class="batch-item-check" checked title="是否加入">
            <input class="batch-item-word" value="${escapeHtml(it.word)}" aria-label="词条">
            <input class="batch-item-meaning" value="${escapeHtml(it.meaning || '')}" placeholder="释义（可编辑）" aria-label="释义">
            <select class="batch-item-level" aria-label="等级">
              ${['CET4','CET6','other'].map(l => `<option value="${l}" ${it.level === l ? 'selected' : ''}>${l}</option>`).join('')}
            </select>
            <button class="batch-item-remove" title="移除">×</button>
          </div>`).join('')}
      </div>
      <div class="batch-preview-actions">
        <label class="batch-preview-select"><input type="checkbox" id="batchPreviewAll" checked> 全选</label>
        <span class="hint" id="batchPreviewCount">已选 ${items.length} 个</span>
        <div class="batch-preview-spacer"></div>
        <button class="toolbar-btn" id="batchPreviewCancel">取消</button>
        <button class="toolbar-btn primary" id="batchPreviewConfirm">确认加入</button>
      </div>`;

    const updateCount = () => {
      const checks = $$('.batch-item-check');
      const checked = checks.filter(c => c.checked).length;
      $('#batchPreviewCount').textContent = `已选 ${checked} 个`;
      $('#batchPreviewAll').checked = checked === checks.length && checks.length > 0;
    };
    $$('.batch-item-check').forEach(c => c.addEventListener('change', updateCount));
    $('#batchPreviewAll').addEventListener('change', e => {
      $$('.batch-item-check').forEach(c => { c.checked = e.target.checked; });
      updateCount();
    });
    $$('.batch-item-remove').forEach(btn => btn.addEventListener('click', () => {
      btn.closest('.batch-preview-item').remove();
      updateCount();
    }));
    $('#batchPreviewCancel').addEventListener('click', () => { box.style.display = 'none'; box.innerHTML = ''; });
    $('#batchPreviewConfirm').addEventListener('click', async () => {
      const selected = $$('.batch-preview-item').filter(row => row.querySelector('.batch-item-check').checked);
      const normalized = selected.map(row => ({
        word: row.querySelector('.batch-item-word').value.trim(),
        meaning: row.querySelector('.batch-item-meaning').value.trim(),
        level: row.querySelector('.batch-item-level').value,
      })).filter(i => i.word);
      if (!normalized.length) { toast('请至少保留一个词条'); return; }
      const btn = $('#batchPreviewConfirm');
      btn.disabled = true;
      btn.textContent = '加入中…';
      try {
        const r = await api.post('/api/words/batch-add', { items: normalized });
        toast(batchAddSummary(r));
        $('#batchWords').value = '';
        loadVocab();
      } catch (e) {
        toast(e.message || '批量添加失败');
        btn.disabled = false;
        btn.textContent = '确认加入';
      }
    });
  }

  function batchAddSummary(r) {
    const parts = [];
    if (r.added) parts.push(`新增 ${r.added}`);
    if (r.activated) parts.push(`加入学习 ${r.activated}`);
    if (r.skipped) parts.push(`跳过 ${r.skipped}`);
    return parts.length ? parts.join(' · ') : '没有可加入的词条';
  }

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

}

function updateVocabToolbar() {
  const toolbar = $('#vocabToolbar');
  if (!toolbar) return;
  const count = selectedWordIds.size;
  const hasSelection = count > 0;
  // 工具栏常驻显示：之前默认隐藏，导致用户根本找不到批量操作入口。
  // 改为常驻 + 未选中时按钮置灰，功能一眼可见。
  toolbar.style.display = 'flex';
  const counter = $('#selectedCount');
  if (counter) counter.textContent = hasSelection ? `已选 ${count} 个` : '勾选单词后可批量操作';
  const knownBtn = $('#batchKnownBtn');
  const delBtn = $('#batchDeleteBtn');
  if (knownBtn) knownBtn.disabled = !hasSelection;
  if (delBtn) delBtn.disabled = !hasSelection;
  const selectAll = $('#selectAllVocab');
  if (selectAll) {
    const total = $$('.vocab-checkbox').length;
    selectAll.checked = hasSelection && count === total;
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

  // 分区与选项错峰浮现，和全站节奏保持一致
  staggerIn('.srs-review-banner');
  staggerIn('.test-type-card', 0.04);
  staggerIn('.test-option-btn', 0.03);
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
  // 选项错峰浮现
  staggerIn('.test-option', 0.035);

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

// 新手引导箭头：没触发过右侧抽屉才显示，首次把抽屉唤出后永久记住（不再打扰）
const guideArrow = $('#rightGuideArrow');
const GUIDE_SEEN_KEY = 'readloops_right_guide_seen';
if (guideArrow && !localStorage.getItem(GUIDE_SEEN_KEY)) {
  setTimeout(() => guideArrow.classList.add('show'), 900);
}
function dismissGuideArrow() {
  if (!guideArrow || guideArrow.classList.contains('dismiss')) return;
  guideArrow.classList.remove('show');
  guideArrow.classList.add('dismiss');
  localStorage.setItem(GUIDE_SEEN_KEY, '1');
}

// 鼠标移入右侧边缘触发（带防抖，避免快速划过误触）
rightPanelTrigger.addEventListener('mouseenter', () => {
  clearTimeout(panelHoverTimer);
  panelHoverTimer = setTimeout(() => {
    if (!panelPinned) {
      rightPanel.classList.add('show');
      loadLookupHistory();
      dismissGuideArrow(); // 用户已发现右侧抽屉，引导箭头退场
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
  dismissGuideArrow();
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

// ============================================================
// 知识库：书架 / 材料 / 知识图谱
// 三者串成一条链路：书架拿书 → 导入为材料 → 蒸馏出风格画像 → 图谱沉淀关联
// ============================================================

const TYPE_LABEL = { word: '词', phrase: '短语', article: '文章', material: '材料', book: '书' };

// ---------------------------------------------------------------- 书架

let libraryQuery = '';

async function loadLibrary() {
  const books = await api.get('/api/books');
  const cards = books.length === 0
    ? `<div class="kb-empty">书架还是空的。搜一本公版书试试——这里的书都可以合法下载与自由使用。</div>`
    : books.map(b => `
      <div class="kb-card" data-book-id="${b.id}">
        <div class="kb-card-main">
          <div class="kb-card-title">${escapeHtml(b.title)}</div>
          <div class="kb-card-meta">
            ${b.author ? escapeHtml(b.author) + ' · ' : ''}${b.word_count ? b.word_count.toLocaleString() + ' 词 · ' : ''}
            <span class="kb-badge kb-badge-${b.status}">${bookStatusText(b.status)}</span>
          </div>
        </div>
        <div class="kb-card-actions">
          ${b.status !== 'imported' ? `<button class="kb-mini-btn" data-act="import" data-id="${b.id}">导入材料</button>` : ''}
          <button class="kb-mini-btn kb-mini-danger" data-act="del" data-id="${b.id}">移除</button>
        </div>
      </div>`).join('');

  $('#reader').innerHTML = `
    <h1>书架</h1>
    <p class="kb-sub">收录公共领域书籍，可自由下载与使用。下载后点「导入材料」，就能参与蒸馏和出题。</p>

    <div class="kb-search">
      <input type="text" id="bookSearchInput" placeholder="搜索书名或作者，例如 alice / sherlock holmes" value="${escapeHtml(libraryQuery)}">
      <button class="toolbar-btn primary" id="bookSearchBtn">搜索</button>
    </div>
    <div id="bookSearchResults"></div>

    <div class="kb-section-title">我的书架 <span class="kb-count">${books.length}</span></div>
    <div class="kb-list">${cards}</div>
  `;

  $('#bookSearchBtn').addEventListener('click', doBookSearch);
  $('#bookSearchInput').addEventListener('keydown', e => {
    if (e.key === 'Enter') doBookSearch();
  });
  if (libraryQuery) doBookSearch();

  $$('.kb-card-actions .kb-mini-btn').forEach(btn => {
    btn.addEventListener('click', async e => {
      e.stopPropagation();
      const id = btn.dataset.id;
      if (btn.dataset.act === 'import') {
        btn.disabled = true;
        btn.textContent = '导入中…';
        const r = await api.post(`/api/books/${id}/import`, {});
        if (r.ok) {
          toast(`已导入，${r.word_count.toLocaleString()} 词`);
          loadLibrary();
        } else {
          toast(r.detail || r.error || '导入失败');
          btn.disabled = false;
          btn.textContent = '导入材料';
        }
      } else if (btn.dataset.act === 'del') {
        if (!await confirmDialog('确定从书架移除这本书吗？本地文件也会删除。', { danger: true })) return;
        await api.del(`/api/books/${id}`);
        toast('已移除');
        loadLibrary();
      }
    });
  });
}

function bookStatusText(s) {
  return { downloaded: '已下载', imported: '已导入', parsed: '已解析', failed: '失败' }[s] || s;
}

async function doBookSearch() {
  const q = $('#bookSearchInput').value.trim();
  libraryQuery = q;
  const box = $('#bookSearchResults');
  if (!q) { box.innerHTML = ''; return; }

  box.innerHTML = `<div class="kb-loading"><span class="loading"></span> 正在搜索…</div>`;
  let data;
  try {
    data = await api.get(`/api/books/search?q=${encodeURIComponent(q)}`);
  } catch (e) {
    box.innerHTML = `<div class="kb-empty">搜索失败：${escapeHtml(e.message || '书源不可达')}</div>`;
    return;
  }
  if (!data.results || data.results.length === 0) {
    box.innerHTML = `<div class="kb-empty">没找到相关书籍，换个关键词试试。</div>`;
    return;
  }

  box.innerHTML = `
    <div class="kb-section-title">搜索结果 <span class="kb-count">${data.results.length}</span></div>
    <div class="kb-list">
      ${data.results.map(b => `
        <div class="kb-card kb-card-result">
          <div class="kb-card-main">
            <div class="kb-card-title">${escapeHtml(b.title)}</div>
            <div class="kb-card-meta">
              ${b.author ? escapeHtml(b.author) + ' · ' : ''}${b.downloads ? b.downloads.toLocaleString() + ' 次下载' : ''}
            </div>
          </div>
          <div class="kb-card-actions">
            <button class="kb-mini-btn kb-mini-primary" data-act="dl"
                    data-sid="${b.source_id}" data-title="${escapeHtml(b.title)}"
                    data-author="${escapeHtml(b.author || '')}">下载</button>
          </div>
        </div>`).join('')}
    </div>
  `;

  $$('#bookSearchResults .kb-mini-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      btn.textContent = '下载中…';
      try {
        const r = await api.post('/api/books/download', {
          source_id: btn.dataset.sid,
          title: btn.dataset.title,
          author: btn.dataset.author,
        });
        toast(`已下载：${r.word_count.toLocaleString()} 词`);
        loadLibrary();
      } catch (e) {
        toast(e.message || '下载失败');
        btn.disabled = false;
        btn.textContent = '下载';
      }
    });
  });
}

// ---------------------------------------------------------------- 材料

async function loadMaterials() {
  const [items, profiles] = await Promise.all([
    api.get('/api/materials'),
    api.get('/api/materials/profiles/all'),
  ]);

  const list = items.length === 0
    ? `<div class="kb-empty">还没有材料。粘贴一段文本，或从书架导入一本书。</div>`
    : items.map(m => `
      <div class="kb-card" data-mid="${m.id}">
        <div class="kb-card-main">
          <div class="kb-card-title">${escapeHtml(m.title)}</div>
          <div class="kb-card-meta">
            ${m.parse_status === 'failed'
              ? `<span class="kb-badge kb-badge-failed">解析失败</span>`
              : `${(m.word_count || 0).toLocaleString()} 词 · ${m.sentence_count || 0} 句 · ${m.para_count || 0} 段`}
          </div>
          ${m.parse_status === 'failed' && m.error ? `<div class="kb-error">${escapeHtml(m.error)}</div>` : ''}
        </div>
        <div class="kb-card-actions">
          <label class="kb-check"><input type="checkbox" class="mat-pick" value="${m.id}"></label>
          <button class="kb-mini-btn kb-mini-danger" data-act="del" data-id="${m.id}">删除</button>
        </div>
      </div>`).join('');

  const profileCards = profiles.length === 0
    ? `<div class="kb-empty">还没有画像。勾选材料后点「蒸馏」生成。</div>`
    : profiles.map(p => `
      <div class="kb-card kb-card-profile ${p.is_active ? 'is-active' : ''}">
        <div class="kb-card-main">
          <div class="kb-card-title">${escapeHtml(p.name)}${p.is_active ? ' <span class="kb-badge kb-badge-active">生效中</span>' : ''}</div>
          <div class="kb-card-meta">${p.sample_count.toLocaleString()} 句 · 平均句长 ${(p.params.avg_len || 0)} 词 · 复合句 ${Math.round((p.params.compound_ratio || 0) * 100)}%</div>
        </div>
        <div class="kb-card-actions">
          ${p.is_active ? '' : `<button class="kb-mini-btn kb-mini-primary" data-act="activate" data-id="${p.id}">设为生效</button>`}
          <button class="kb-mini-btn kb-mini-danger" data-act="del-profile" data-id="${p.id}">删除</button>
        </div>
      </div>`).join('');

  $('#reader').innerHTML = `
    <h1>材料</h1>
    <p class="kb-sub">材料只留在本机，用于蒸馏与出题。材料越多，风格画像越准。</p>

    <div class="kb-panel">
      <div class="kb-panel-title">粘贴导入</div>
      <input type="text" id="matTitle" class="kb-input" placeholder="材料标题（可留空）">
      <textarea id="matContent" class="kb-textarea" placeholder="把英文文本粘贴到这里…"></textarea>
      <div class="kb-panel-actions">
        <button class="toolbar-btn primary" id="matPasteBtn">导入</button>
      </div>
    </div>

    <div class="kb-panel">
      <div class="kb-panel-title">从本地文件导入</div>
      <input type="text" id="matPath" class="kb-input" placeholder="文件绝对路径，支持 .txt / .md / .epub / .pdf">
      <div class="kb-panel-actions">
        <button class="toolbar-btn" id="matFileBtn">导入文件</button>
      </div>
      <div class="kb-hint">PDF 需要额外依赖：轻量用 <code>pip install pypdf</code>，扫描件/复杂版式建议 <code>pip install 'mineru[core]'</code>。</div>
    </div>

    <div class="kb-section-title">材料 <span class="kb-count">${items.length}</span></div>
    <div class="kb-list">${list}</div>

    <div class="kb-panel">
      <div class="kb-panel-title">蒸馏</div>
      <div class="kb-hint">勾选上面的材料（可多选），点蒸馏生成风格画像。多份材料会合并统计，分布更真实。</div>
      <div class="kb-panel-actions">
        <button class="toolbar-btn primary" id="distillBtn">蒸馏选中材料</button>
        <span class="kb-hint-inline" id="pickCount">已选 0 份</span>
      </div>
    </div>

    <div class="kb-section-title">风格画像 <span class="kb-count">${profiles.length}</span></div>
    <div class="kb-list">${profileCards}</div>
  `;

  const picks = $$('.mat-pick');
  const updatePick = () => {
    $('#pickCount').textContent = `已选 ${picks.filter(p => p.checked).length} 份`;
  };
  picks.forEach(p => p.addEventListener('change', updatePick));

  $('#matPasteBtn').addEventListener('click', async () => {
    const content = $('#matContent').value.trim();
    if (!content) { toast('请先粘贴内容'); return; }
    const r = await api.post('/api/materials/paste', {
      title: $('#matTitle').value.trim(), content,
    });
    toast(`已导入，${r.word_count.toLocaleString()} 词`);
    loadMaterials();
  });

  $('#matFileBtn').addEventListener('click', async () => {
    const path = $('#matPath').value.trim();
    if (!path) { toast('请填写文件路径'); return; }
    const r = await api.post('/api/materials/import', { path });
    if (r.ok) {
      toast(`已导入，${r.word_count.toLocaleString()} 词`);
      loadMaterials();
    } else {
      await showAlert(r.detail || r.error || '导入失败');
      loadMaterials();
    }
  });

  $('#distillBtn').addEventListener('click', async () => {
    const ids = picks.filter(p => p.checked).map(p => parseInt(p.value));
    if (!ids.length) { toast('请先勾选材料'); return; }
    const btn = $('#distillBtn');
    btn.disabled = true;
    btn.textContent = '蒸馏中…';
    try {
      const r = await api.post('/api/materials/distill', { material_ids: ids, activate: true });
      toast(`已生成画像，样本 ${r.params.sentence_count} 句`);
      loadMaterials();
    } catch (e) {
      toast(e.message || '蒸馏失败');
      btn.disabled = false;
      btn.textContent = '蒸馏选中材料';
    }
  });

  $$('.kb-card-actions .kb-mini-btn').forEach(btn => {
    btn.addEventListener('click', async e => {
      e.stopPropagation();
      const id = btn.dataset.id;
      if (btn.dataset.act === 'del') {
        if (!await confirmDialog('确定删除这份材料吗？', { danger: true })) return;
        await api.del(`/api/materials/${id}`);
        toast('已删除');
        loadMaterials();
      } else if (btn.dataset.act === 'activate') {
        await api.post(`/api/materials/profiles/${id}/activate`, {});
        toast('已设为生效画像');
        loadMaterials();
      } else if (btn.dataset.act === 'del-profile') {
        if (!await confirmDialog('确定删除这个画像吗？', { danger: true })) return;
        await api.del(`/api/materials/profiles/${id}`);
        toast('已删除');
        loadMaterials();
      }
    });
  });
}

// ---------------------------------------------------------------- 知识图谱

let graphState = null;

async function loadGraph() {
  $('#reader').innerHTML = `
    <h1>知识图谱</h1>
    <p class="kb-sub">你的词、短语与文章连成一张网。点节点看关联，拖拽可以调整位置。</p>
    <div class="kb-toolbar">
      <button class="toolbar-btn" id="graphRebuildBtn">重建图谱</button>
      <div class="kb-filters" id="graphFilters">
        <label class="kb-check"><input type="checkbox" value="word" checked> 词</label>
        <label class="kb-check"><input type="checkbox" value="phrase" checked> 短语</label>
        <label class="kb-check"><input type="checkbox" value="article" checked> 文章</label>
        <label class="kb-check"><input type="checkbox" value="material" checked> 材料</label>
      </div>
      <span class="kb-hint-inline" id="graphStats"></span>
    </div>
    <div class="graph-stage" id="graphStage">
      <canvas id="graphCanvas"></canvas>
      <div class="graph-tip" id="graphTip"></div>
    </div>
  `;

  $('#graphRebuildBtn').addEventListener('click', async () => {
    const btn = $('#graphRebuildBtn');
    btn.disabled = true;
    btn.textContent = '重建中…';
    const s = await api.post('/api/graph/rebuild', {});
    toast(`图谱已重建：${s.nodes} 节点 / ${s.edges} 边`);
    btn.disabled = false;
    btn.textContent = '重建图谱';
    drawGraph();
  });

  $$('#graphFilters input').forEach(cb => cb.addEventListener('change', drawGraph));

  await drawGraph();
}

async function drawGraph() {
  const types = $$('#graphFilters input').filter(c => c.checked).map(c => c.value);
  const canvas = $('#graphCanvas');
  const stage = $('#graphStage');
  if (!canvas || !stage) return;

  if (!types.length) {
    $('#graphStats').textContent = '请至少选择一种节点类型';
    graphState = null;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    return;
  }

  const data = await api.get(`/api/graph?types=${types.join(',')}&limit=400`);
  $('#graphStats').textContent = `${data.nodes.length} 节点 · ${data.edges.length} 边`;

  if (!data.nodes.length) {
    $('#graphStats').textContent = '图谱是空的，点「重建图谱」试试';
    graphState = null;
    return;
  }

  const dpr = window.devicePixelRatio || 1;
  const W = stage.clientWidth, H = stage.clientHeight;
  canvas.width = W * dpr;
  canvas.height = H * dpr;
  canvas.style.width = W + 'px';
  canvas.style.height = H + 'px';
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  // 初始布局：按类型分环，避免全部堆在中心
  const typeOrder = ['article', 'material', 'phrase', 'word'];
  const nodes = data.nodes.map((n, i) => {
    const ring = Math.max(1, typeOrder.indexOf(n.type) + 1);
    const a = (i / data.nodes.length) * Math.PI * 2 + ring;
    const r = Math.min(W, H) * 0.12 * ring;
    return {
      ...n,
      x: W / 2 + Math.cos(a) * r,
      y: H / 2 + Math.sin(a) * r,
      vx: 0, vy: 0,
      deg: 0,
    };
  });
  const byId = new Map(nodes.map(n => [n.id, n]));
  const links = data.edges
    .map(e => ({ s: byId.get(e.source), t: byId.get(e.target), w: e.weight, type: e.type }))
    .filter(l => l.s && l.t);
  links.forEach(l => { l.s.deg++; l.t.deg++; });

  const palette = {
    word: getComputedStyle(document.documentElement).getPropertyValue('--text').trim() || '#fdfcfc',
    phrase: getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#8c82ff',
    article: '#5dcaa5',
    material: '#ef9f27',
  };

  graphState = { nodes, links, byId, ctx, W, H, palette, drag: null, hover: null, selected: null };

  // ---- 物理迭代（弹簧 + 斥力），在动画帧里逐步收敛
  function tick() {
    const st = graphState;
    if (!st) return;
    const { nodes, links } = st;
    const k = 0.02;
    // 斥力
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        let dx = b.x - a.x, dy = b.y - a.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { d2 = 1; dx = Math.random() - 0.5; dy = Math.random() - 0.5; }
        const f = 900 / d2;
        const d = Math.sqrt(d2);
        const fx = (dx / d) * f, fy = (dy / d) * f;
        a.vx -= fx; a.vy -= fy;
        b.vx += fx; b.vy += fy;
      }
    }
    // 弹簧
    for (const l of links) {
      const dx = l.t.x - l.s.x, dy = l.t.y - l.s.y;
      const d = Math.max(1, Math.hypot(dx, dy));
      const f = (d - 70) * k * Math.min(2, l.w / 5);
      const fx = (dx / d) * f, fy = (dy / d) * f;
      l.s.vx += fx; l.s.vy += fy;
      l.t.vx -= fx; l.t.vy -= fy;
    }
    // 向心 + 阻尼
    for (const n of nodes) {
      n.vx += (st.W / 2 - n.x) * 0.0016;
      n.vy += (st.H / 2 - n.y) * 0.0016;
      n.vx *= 0.86; n.vy *= 0.86;
      if (st.drag === n) { n.vx = 0; n.vy = 0; continue; }
      n.x += n.vx; n.y += n.vy;
      n.x = Math.max(20, Math.min(st.W - 20, n.x));
      n.y = Math.max(20, Math.min(st.H - 20, n.y));
    }
  }

  function render() {
    const st = graphState;
    if (!st) return;
    const { ctx, W, H, nodes, links, palette } = st;
    ctx.clearRect(0, 0, W, H);

    ctx.lineWidth = 0.6;
    for (const l of links) {
      const near = st.hover && (l.s === st.hover || l.t === st.hover);
      ctx.strokeStyle = near ? 'rgba(140,130,255,0.75)' : 'rgba(140,130,255,0.16)';
      ctx.beginPath();
      ctx.moveTo(l.s.x, l.s.y);
      ctx.lineTo(l.t.x, l.t.y);
      ctx.stroke();
    }

    for (const n of nodes) {
      const base = n.type === 'word' ? 3 : 5;
      const r = base + Math.min(9, Math.sqrt(n.deg) * 1.7) + Math.min(5, n.weight * 0.25);
      const isHi = n === st.hover || n === st.selected;
      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
      ctx.fillStyle = palette[n.type] || '#888';
      ctx.globalAlpha = isHi ? 1 : 0.82;
      ctx.fill();
      ctx.globalAlpha = 1;
      if (isHi) {
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = '#fff';
        ctx.stroke();
      }
    }

    // 只给权重高的节点画标签，避免糊成一片
    const labeled = nodes.filter(n => n.deg >= 3 || n.weight >= 2).slice(0, 70);
    ctx.font = '11px ui-monospace, monospace';
    ctx.textAlign = 'center';
    for (const n of labeled) {
      ctx.fillStyle = n === st.hover ? '#fff' : 'rgba(253,252,252,0.55)';
      ctx.fillText(n.label.slice(0, 16), n.x, n.y - 9 - Math.min(6, n.deg * 0.4));
    }
  }

  function loop() {
    if (!graphState) return;
    tick();
    render();
    requestAnimationFrame(loop);
  }

  // ---- 交互：悬停高亮 + 拖拽
  function pick(x, y) {
    const st = graphState;
    if (!st) return null;
    let best = null, bestD = 400;
    for (const n of st.nodes) {
      const d = (n.x - x) ** 2 + (n.y - y) ** 2;
      if (d < bestD) { bestD = d; best = n; }
    }
    return best;
  }

  function localPos(e) {
    const r = canvas.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }

  canvas.onmousemove = (e) => {
    const st = graphState;
    if (!st) return;
    const [x, y] = localPos(e);
    if (st.drag) {
      st.drag.x = x; st.drag.y = y;
      return;
    }
    const hit = pick(x, y);
    st.hover = hit;
    canvas.style.cursor = hit ? 'pointer' : 'default';
    const tip = $('#graphTip');
    if (hit) {
      tip.style.display = 'block';
      tip.style.left = (x + 14) + 'px';
      tip.style.top = (y + 14) + 'px';
      const m = hit.meta || {};
      tip.innerHTML = `<b>${escapeHtml(hit.label)}</b><br>${TYPE_LABEL[hit.type] || hit.type}` +
        (m.level ? ` · ${escapeHtml(m.level)}` : '') +
        (m.meaning ? `<br>${escapeHtml(String(m.meaning).slice(0, 80))}` : '') +
        (m.status ? `<br>状态：${escapeHtml(m.status)}` : '');
    } else {
      tip.style.display = 'none';
    }
  };

  canvas.onmousedown = (e) => {
    const st = graphState;
    if (!st) return;
    const [x, y] = localPos(e);
    const hit = pick(x, y);
    if (hit) { st.drag = hit; st.selected = hit; }
  };

  window.onmouseup = () => { if (graphState) graphState.drag = null; };

  canvas.onmouseleave = () => {
    if (graphState) graphState.hover = null;
    const tip = $('#graphTip');
    if (tip) tip.style.display = 'none';
  };

  if (!graphState._looping) {
    graphState._looping = true;
    loop();
  }
}
