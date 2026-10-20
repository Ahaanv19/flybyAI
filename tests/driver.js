// In-page smoke test driver. tests/smoke.py evaluates this inside the loaded
// page over the DevTools protocol and awaits the promise it returns.
(async () => {
  const results = [];
  const check = (name, ok, detail) =>
    results.push({ name, ok: Boolean(ok), detail: detail == null ? '' : String(detail) });

  const $ = sel => document.querySelector(sel);
  const $$ = sel => Array.from(document.querySelectorAll(sel));
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const jump = top => window.scrollTo({ top, behavior: 'instant' });
  const absTop = el => el.getBoundingClientRect().top + window.scrollY;
  const shown = el => !!el && getComputedStyle(el).display !== 'none' && el.getBoundingClientRect().width > 0;
  const describe = el => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +
    (typeof el.className === 'string' && el.className.trim() ? '.' + el.className.trim().split(/\s+/).join('.') : '');
  const mobile = window.innerWidth < 768;

  const slides = $$('.c-slide');
  const dots = $$('.c-dot');
  const activeIdx = () => slides.findIndex(s => s.classList.contains('active'));
  const SETTLE = 700; // the carousel ignores input for 580ms after each move

  async function run() {
    // ── Carousel
    check('carousel starts on the first slide', activeIdx() === 0, `active slide ${activeIdx()}`);
    $('#cNext').click(); await wait(SETTLE);
    check('next arrow advances the carousel', activeIdx() === 1, `active slide ${activeIdx()}`);
    check('URL bar tracks the active slide', $('#carouselUrl').textContent === slides[1].dataset.url, $('#carouselUrl').textContent);
    $('#cPrev').click(); await wait(SETTLE);
    check('prev arrow goes back', activeIdx() === 0, `active slide ${activeIdx()}`);
    dots[3].click(); await wait(SETTLE);
    check('dots jump to their slide', activeIdx() === 3 && dots[3].classList.contains('active'), `active slide ${activeIdx()}`);
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight' })); await wait(SETTLE);
    check('arrow keys move the carousel', activeIdx() === 4, `active slide ${activeIdx()}`);
    const before = activeIdx(); await wait(4500);
    check('carousel autoplays', activeIdx() !== before, `still on slide ${before}`);

    const navItems = $$('.c-slide.active .m-nav-item');
    navItems[2].click();
    check('mockup sidebar items are clickable', navItems[2].classList.contains('active') && !navItems[0].classList.contains('active'));

    // ── Navigation
    if (mobile) {
      check('hamburger is shown on mobile', shown($('#hamburger')));
      check('desktop nav links are hidden on mobile', !shown($('.nav-links')));
    } else {
      check('nav links are shown on desktop', shown($('.nav-links')));
      check('hamburger is hidden on desktop', !shown($('#hamburger')));
    }
    $('#hamburger').click();
    check('hamburger opens the mobile menu', $('#mobileNav').classList.contains('open'));
    const menuLink = $('#mobileNav a');
    menuLink.addEventListener('click', e => e.preventDefault(), { once: true });
    menuLink.click();
    check('menu links close the mobile menu', !$('#mobileNav').classList.contains('open') && !$('#hamburger').classList.contains('open'));

    // ── Scroll-driven behaviour
    check('hero content is revealed on load', $$('.hero .reveal.visible').length > 0, `${$$('.hero .reveal.visible').length} revealed`);
    jump(400); await wait(300);
    check('nav switches to its scrolled style', $('#navbar').classList.contains('scrolled'));
    jump(0); await wait(300);
    check('nav returns to its top style', !$('#navbar').classList.contains('scrolled'));

    jump(document.getElementById('how').offsetTop); await wait(300);
    const howLink = $('.nav-links a[href="#how"]');
    check('nav highlights the current section', howLink.style.color === 'var(--blue)', howLink.style.color || 'no highlight');

    const nums = $$('.stat-num[data-count]');
    for (const el of nums) { jump(absTop(el) - innerHeight / 2); await wait(150); }
    await wait(1900);
    check('stat counters land on their targets',
      nums.length > 0 && nums.every(el => el.textContent.trim() === el.dataset.count),
      nums.map(el => `${el.textContent.trim()} (want ${el.dataset.count})`).join(', '));

    const card = $('.feat-card');
    jump(absTop(card) - 100); await wait(100);
    const r = card.getBoundingClientRect();
    card.dispatchEvent(new MouseEvent('mousemove', { clientX: r.left + r.width * 0.8, clientY: r.top + r.height * 0.2, bubbles: true }));
    check('feature cards tilt on hover', /rotateX/.test(card.style.transform), card.style.transform || 'no transform');
    card.dispatchEvent(new MouseEvent('mouseleave'));
    check('feature cards reset after hover', card.style.transform === '', card.style.transform);

    // ── Waitlist + layout
    const ctas = $$('[data-tally-open]');
    check('waitlist buttons are visible', ctas.length > 0 && ctas.every(shown), `${ctas.filter(shown).length}/${ctas.length} visible`);

    jump(0); await wait(100);
    // body has overflow-x:hidden, which would mask overflow; lift it to measure.
    document.body.style.overflowX = 'visible';
    const viewportWidth = document.documentElement.clientWidth;
    const contentWidth = document.documentElement.scrollWidth;
    let culprits = [];
    if (contentWidth > viewportWidth) {
      const clipped = el => { for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) { if (getComputedStyle(a).overflowX !== 'visible') return true; } return false; };
      culprits = $$('body *').filter(el => el.getBoundingClientRect().right > viewportWidth + 0.5 && !clipped(el)).slice(0, 4).map(describe);
    }
    document.body.style.overflowX = '';
    check('no horizontal overflow', contentWidth <= viewportWidth,
      `${contentWidth}px of content in a ${viewportWidth}px viewport` + (culprits.length ? `; widest: ${culprits.join(', ')}` : ''));

    check('no uncaught JS errors', (window.__smokeErrors || []).length === 0, (window.__smokeErrors || []).join(' | '));
  }

  try {
    await run();
  } catch (err) {
    check('smoke driver finished', false, (err && err.stack) || err);
  }
  return { viewport: `${innerWidth}x${innerHeight}`, results };
})()
