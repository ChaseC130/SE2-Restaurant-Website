document.addEventListener('DOMContentLoaded', () => {
  const chips = document.querySelectorAll('[data-filter]');
  const items = document.querySelectorAll('.menu-item');
  chips.forEach(chip => {
    chip.addEventListener('click', () => {
      const filter = chip.dataset.filter;
      chips.forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      items.forEach(item => {
        item.style.display = (filter === 'all' || item.dataset.category === filter) ? '' : 'none';
      });
    });
  });
});
