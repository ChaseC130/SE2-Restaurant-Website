document.addEventListener('DOMContentLoaded', () => {
  const filter = document.getElementById('categoryFilter');
  if (!filter) return;

  filter.addEventListener('change', () => {
    const selected = filter.value;
    document.querySelectorAll('.menu-card').forEach((card) => {
      const match = selected === 'all' || card.dataset.category === selected;
      card.style.display = match ? 'block' : 'none';
    });
  });
});
