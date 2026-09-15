/* ========================================
   DARK MODE TOGGLE
   ======================================== */

document.addEventListener('DOMContentLoaded', function() {
  // Initialize dark mode from localStorage
  const isDarkMode = localStorage.getItem('darkMode') === 'true';
  if (isDarkMode) {
    document.body.classList.add('dark-mode');
    updateThemeToggleButton();
  }

  // Setup theme toggle button listener
  const themeToggle = document.querySelector('.theme-toggle');
  if (themeToggle) {
    themeToggle.addEventListener('click', toggleDarkMode);
  }
});

function toggleDarkMode() {
  const isDarkMode = document.body.classList.toggle('dark-mode');
  localStorage.setItem('darkMode', isDarkMode);
  updateThemeToggleButton();
}

function updateThemeToggleButton() {
  const themeToggle = document.querySelector('.theme-toggle');
  if (themeToggle) {
    const isDarkMode = document.body.classList.contains('dark-mode');
    themeToggle.textContent = isDarkMode ? '☀️ Light Mode' : '🌙 Dark Mode';
    themeToggle.setAttribute('title', isDarkMode ? 'Switch to Light Mode' : 'Switch to Dark Mode');
  }
}
