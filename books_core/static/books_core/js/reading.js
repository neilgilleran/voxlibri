/**
 * Reading View JavaScript
 * Handles chapter navigation toggle on mobile (Phase 1 compatibility)
 * Note: Phase 5 AI features are in reading-ai.js
 */

document.addEventListener('DOMContentLoaded', function() {
    // This file is kept for backward compatibility
    // The navigation toggle is now handled in reading-ai.js for Phase 5
    // If reading-ai.js is not loaded, this provides fallback functionality

    // Check if reading-ai.js is loaded
    if (window.VoxLibriReading) {
        // reading-ai.js is loaded, skip duplicate initialization
        return;
    }

    // Fallback: Basic navigation toggle for Phase 1 compatibility
    const toggleBtn = document.getElementById('toggle-nav');
    const chapterNav = document.getElementById('chapter-nav');

    if (toggleBtn && chapterNav) {
        // Toggle chapter navigation on mobile
        toggleBtn.addEventListener('click', function() {
            chapterNav.classList.toggle('open');
        });

        // Close navigation when clicking a chapter link (mobile)
        const chapterLinks = chapterNav.querySelectorAll('a');
        chapterLinks.forEach(link => {
            link.addEventListener('click', function() {
                // Small delay to allow navigation to start before closing
                setTimeout(() => {
                    chapterNav.classList.remove('open');
                }, 100);
            });
        });

        // Close navigation when clicking outside (mobile)
        document.addEventListener('click', function(event) {
            if (window.innerWidth <= 768) {
                if (!chapterNav.contains(event.target) &&
                    !toggleBtn.contains(event.target) &&
                    chapterNav.classList.contains('open')) {
                    chapterNav.classList.remove('open');
                }
            }
        });
    }
});
