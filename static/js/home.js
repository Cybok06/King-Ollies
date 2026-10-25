document.documentElement.classList.add('js');
const toggle = document.querySelector('.menu-toggle');
const links = document.getElementById('nav-links');
function closeMenu() { links.classList.remove('open'); toggle.setAttribute('aria-expanded', 'false'); }
toggle.addEventListener('click', () => {
  const open = links.classList.toggle('open');
  toggle.setAttribute('aria-expanded', String(open));
});
links.querySelectorAll('a').forEach(link => link.addEventListener('click', closeMenu));
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && links.classList.contains('open')) { closeMenu(); toggle.focus(); }
});
document.querySelectorAll('[data-dialog]').forEach(button => {
  button.addEventListener('click', () => document.getElementById(button.dataset.dialog).showModal());
});
document.querySelectorAll('dialog').forEach(dialog => {
  dialog.addEventListener('click', event => {
    if (event.target === dialog) {
      const bounds = dialog.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
    }
  });
});
document.getElementById('yearNow').textContent = new Date().getFullYear();

// Service images are supplied by the landing route from the services collection.
const slideshow = document.querySelector('.service-slideshow');
if (slideshow) {
  const slides = JSON.parse(document.getElementById('hero-service-slides').textContent);
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const control = document.getElementById('hero-slideshow-toggle');
  let index = 0, paused = reducedMotion.matches, timer;
  function advance() {
    if (slides.length < 2) return;
    index = (index + 1) % slides.length;
    const previous = slideshow.querySelector('.hero-service-slide');
    const next = previous.cloneNode();
    next.removeAttribute('fetchpriority');
    next.src = slides[index].src;
    next.alt = slides[index].name;
    next.classList.add('incoming');
    next.style.transform = '';
    slideshow.insertBefore(next, previous);
    requestAnimationFrame(() => requestAnimationFrame(() => {
      next.style.transform = 'translateX(0)';
      previous.style.transform = 'translateX(-100%)';
    }));
    setTimeout(() => { previous.remove(); next.classList.remove('incoming'); }, 450);
    document.getElementById('hero-service-name').textContent = slides[index].name;
  }
  function updateTimer() {
    clearInterval(timer);
    control.textContent = paused ? 'Play' : 'Pause';
    control.setAttribute('aria-label', (paused ? 'Play' : 'Pause') + ' service slideshow');
    if (!paused && !document.hidden) timer = setInterval(advance, 2000);
  }
  control.addEventListener('click', () => { paused = !paused; updateTimer(); });
  reducedMotion.addEventListener('change', () => { paused = reducedMotion.matches; updateTimer(); });
  document.addEventListener('visibilitychange', updateTimer);
  updateTimer();
}

// Show on every page load without saving dismissal.
const welcomeModal = document.getElementById('welcomeModal');
if (welcomeModal) {
  const copy = document.getElementById('welcome-copy');
  const control = document.getElementById('welcome-scroll-toggle');
  const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let paused = motion.matches, resumeAt = performance.now() + 5000;
  let previousTime = null, frame, position = 0;
  function updateControl() { control.textContent = paused ? 'Resume scrolling' : 'Pause scrolling'; }
  function manualScroll() { resumeAt = performance.now() + 6000; position = copy.scrollTop; }
  function advance(now) {
    if (!welcomeModal.open) return;
    if (previousTime !== null && !paused && !document.hidden && now >= resumeAt) {
      position += Math.min(now - previousTime, 100) * 0.018;
      copy.scrollTop = position;
      if (copy.scrollTop >= copy.scrollHeight - copy.clientHeight - 1) {
        paused = true;
        updateControl();
      }
    } else { position = copy.scrollTop; }
    previousTime = now;
    frame = requestAnimationFrame(advance);
  }
  ['wheel', 'pointerdown', 'pointermove', 'touchstart', 'touchmove', 'keydown'].forEach(name => {
    copy.addEventListener(name, manualScroll, { passive: true });
  });
  control.addEventListener('click', () => {
    paused = !paused; resumeAt = performance.now(); position = copy.scrollTop; updateControl();
  });
  motion.addEventListener('change', () => { paused = motion.matches; updateControl(); });
  document.getElementById('welcome-okay').addEventListener('click', () => welcomeModal.close());
  welcomeModal.addEventListener('close', () => {
    cancelAnimationFrame(frame); document.body.classList.remove('welcome-open');
  });
  welcomeModal.showModal();
  document.body.classList.add('welcome-open');
  updateControl();
  frame = requestAnimationFrame(advance);
}
