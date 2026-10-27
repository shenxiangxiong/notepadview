(() => {
  const toggle = document.querySelector('.menu-toggle');
  const navigation = document.querySelector('#primary-navigation');
  if (!toggle || !navigation) return;

  const mobile = window.matchMedia('(max-width: 760px)');
  const closeMenu = (returnFocus = false) => {
    toggle.setAttribute('aria-expanded', 'false');
    navigation.hidden = mobile.matches;
    if (returnFocus) toggle.focus();
  };
  const syncLayout = () => {
    toggle.hidden = !mobile.matches;
    navigation.hidden = mobile.matches &&
      toggle.getAttribute('aria-expanded') !== 'true';
  };

  toggle.hidden = false;
  toggle.addEventListener('click', () => {
    const isExpanded = toggle.getAttribute('aria-expanded') === 'true';
    toggle.setAttribute('aria-expanded', String(!isExpanded));
    navigation.hidden = isExpanded;
  });
  navigation.addEventListener('click', (event) => {
    const link = event.target.closest('a');
    if (!link) return;
    closeMenu();
    const target = document.getElementById(decodeURIComponent(link.hash.slice(1)));
    if (!target) return;
    const destination = target.matches('h1, h2, h3')
      ? target
      : target.querySelector('h1, h2, h3') || target;
    if (!destination.hasAttribute('tabindex')) {
      destination.setAttribute('tabindex', '-1');
    }
    requestAnimationFrame(() => destination.focus({ preventScroll: true }));
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' &&
        toggle.getAttribute('aria-expanded') === 'true') {
      closeMenu(true);
    }
  });
  mobile.addEventListener('change', syncLayout);
  syncLayout();
})();
