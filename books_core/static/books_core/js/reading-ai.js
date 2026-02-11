/**
 * Reading View AI Features JavaScript
 * Handles:
 * - Column resizing with localStorage persistence
 * - Checkbox selection state management
 * - Batch selection controls
 * - Prompt dropdown population (placeholder for Phase 6 HTMX integration)
 */

// Column resizing configuration
const STORAGE_KEY = 'voxlibri_column_widths';
const MIN_COLUMN_WIDTH = {
    left: 200,    // px
    center: 300,  // px
    right: 250    // px
};

// State management
let selectedChapterIds = new Set();
let isResizing = false;
let resizingColumn = null;
let startX = 0;
let startLeftWidth = 0;
let startRightWidth = 0;

/**
 * Initialize the reading view AI features
 */
document.addEventListener('DOMContentLoaded', function() {
    initializeColumnResizing();
    initializeCheckboxSelection();
    initializeBatchControls();
    // Note: Prompts are loaded by modals.js

    // Update navigation toggle to work with new class name
    updateNavigationToggle();
});

/**
 * Update navigation toggle to work with enhanced navigation
 */
function updateNavigationToggle() {
    const toggleBtn = document.getElementById('toggle-nav');
    const chapterNav = document.getElementById('chapter-nav');

    if (toggleBtn && chapterNav) {
        // Replace the old event listener
        const newToggleBtn = toggleBtn.cloneNode(true);
        toggleBtn.parentNode.replaceChild(newToggleBtn, toggleBtn);

        newToggleBtn.addEventListener('click', function() {
            chapterNav.classList.toggle('open');
        });

        // Close navigation when clicking outside (mobile)
        document.addEventListener('click', function(event) {
            if (window.innerWidth <= 1024) {
                if (!chapterNav.contains(event.target) &&
                    !newToggleBtn.contains(event.target) &&
                    chapterNav.classList.contains('open')) {
                    chapterNav.classList.remove('open');
                }
            }
        });
    }
}

/**
 * Initialize column resizing functionality
 */
function initializeColumnResizing() {
    const container = document.querySelector('.reading-container-3col');
    if (!container) return;

    // Load saved widths from localStorage
    loadColumnWidths(container);

    // Set up resize handles
    const leftHandle = document.getElementById('resize-left');
    const rightHandle = document.getElementById('resize-right');

    if (leftHandle) {
        leftHandle.addEventListener('mousedown', (e) => startResize(e, 'left'));
    }

    if (rightHandle) {
        rightHandle.addEventListener('mousedown', (e) => startResize(e, 'right'));
    }

    // Global mouse events for resizing
    document.addEventListener('mousemove', handleResize);
    document.addEventListener('mouseup', stopResize);
}

/**
 * Start column resize
 */
function startResize(event, column) {
    event.preventDefault();
    isResizing = true;
    resizingColumn = column;
    startX = event.clientX;

    const container = document.querySelector('.reading-container-3col');
    const containerRect = container.getBoundingClientRect();
    const leftColumn = container.querySelector('.chapter-navigation-enhanced');
    const rightColumn = container.querySelector('.summary-panel');

    if (leftColumn && rightColumn) {
        startLeftWidth = leftColumn.getBoundingClientRect().width;
        startRightWidth = rightColumn.getBoundingClientRect().width;
    }

    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
}

/**
 * Handle column resize
 */
function handleResize(event) {
    if (!isResizing) return;

    const container = document.querySelector('.reading-container-3col');
    const containerRect = container.getBoundingClientRect();
    const deltaX = event.clientX - startX;
    const containerWidth = containerRect.width;

    if (resizingColumn === 'left') {
        // Resize left column
        const newLeftWidth = startLeftWidth + deltaX;
        const leftPercent = (newLeftWidth / containerWidth) * 100;

        // Enforce minimum widths
        if (newLeftWidth >= MIN_COLUMN_WIDTH.left) {
            // Calculate remaining width for center and right
            const remainingPercent = 100 - leftPercent - ((8 * 2) / containerWidth * 100);
            const centerPercent = remainingPercent * 0.65; // Maintain approximate ratio
            const rightPercent = remainingPercent * 0.35;

            container.style.setProperty('--left-width', `${leftPercent}%`);
            container.style.setProperty('--center-width', `${centerPercent}%`);
            container.style.setProperty('--right-width', `${rightPercent}%`);
        }
    } else if (resizingColumn === 'right') {
        // Resize right column
        const newRightWidth = startRightWidth - deltaX; // Subtract because drag goes opposite
        const rightPercent = (newRightWidth / containerWidth) * 100;

        // Enforce minimum widths
        if (newRightWidth >= MIN_COLUMN_WIDTH.right) {
            // Calculate remaining width for left and center
            const remainingPercent = 100 - rightPercent - ((8 * 2) / containerWidth * 100);
            const leftPercent = remainingPercent * 0.28; // Maintain approximate ratio
            const centerPercent = remainingPercent * 0.72;

            container.style.setProperty('--left-width', `${leftPercent}%`);
            container.style.setProperty('--center-width', `${centerPercent}%`);
            container.style.setProperty('--right-width', `${rightPercent}%`);
        }
    }
}

/**
 * Stop column resize
 */
function stopResize() {
    if (!isResizing) return;

    isResizing = false;
    resizingColumn = null;
    document.body.style.cursor = '';
    document.body.style.userSelect = '';

    // Save current widths to localStorage
    saveColumnWidths();
}

/**
 * Load column widths from localStorage
 */
function loadColumnWidths(container) {
    try {
        const saved = localStorage.getItem(STORAGE_KEY);
        if (saved) {
            const widths = JSON.parse(saved);
            container.style.setProperty('--left-width', widths.left);
            container.style.setProperty('--center-width', widths.center);
            container.style.setProperty('--right-width', widths.right);
        }
    } catch (error) {
        console.error('Error loading column widths:', error);
    }
}

/**
 * Save column widths to localStorage
 */
function saveColumnWidths() {
    try {
        const container = document.querySelector('.reading-container-3col');
        const styles = getComputedStyle(container);
        const widths = {
            left: container.style.getPropertyValue('--left-width') || '20%',
            center: container.style.getPropertyValue('--center-width') || '50%',
            right: container.style.getPropertyValue('--right-width') || 'calc(30% - 16px)'
        };
        localStorage.setItem(STORAGE_KEY, JSON.stringify(widths));
    } catch (error) {
        console.error('Error saving column widths:', error);
    }
}

/**
 * Initialize checkbox selection functionality
 */
function initializeCheckboxSelection() {
    const checkboxes = document.querySelectorAll('.chapter-checkbox');
    const batchButton = document.getElementById('batch-generate');
    const countText = document.getElementById('batch-count-text');

    checkboxes.forEach(checkbox => {
        checkbox.addEventListener('change', function() {
            const chapterId = this.dataset.chapterId;

            if (this.checked) {
                selectedChapterIds.add(chapterId);
            } else {
                selectedChapterIds.delete(chapterId);
            }

            updateBatchUI(batchButton, countText);
        });
    });
}

/**
 * Initialize batch control buttons (Select All / Deselect All)
 */
function initializeBatchControls() {
    const selectAllBtn = document.getElementById('select-all');
    const deselectAllBtn = document.getElementById('deselect-all');
    const batchButton = document.getElementById('batch-generate');
    const countText = document.getElementById('batch-count-text');

    if (selectAllBtn) {
        selectAllBtn.addEventListener('click', function() {
            const checkboxes = document.querySelectorAll('.chapter-checkbox');
            checkboxes.forEach(checkbox => {
                checkbox.checked = true;
                selectedChapterIds.add(checkbox.dataset.chapterId);
            });
            updateBatchUI(batchButton, countText);
        });
    }

    if (deselectAllBtn) {
        deselectAllBtn.addEventListener('click', function() {
            const checkboxes = document.querySelectorAll('.chapter-checkbox');
            checkboxes.forEach(checkbox => {
                checkbox.checked = false;
            });
            selectedChapterIds.clear();
            updateBatchUI(batchButton, countText);
        });
    }
}

/**
 * Update batch generation UI based on selection
 */
function updateBatchUI(batchButton, countText) {
    const count = selectedChapterIds.size;

    if (batchButton) {
        if (count >= 2) {
            batchButton.disabled = false;
            batchButton.textContent = `Batch Generate (${count})`;
        } else {
            batchButton.disabled = true;
            batchButton.textContent = 'Batch Generate';
        }
    }

    if (countText) {
        if (count === 0) {
            countText.textContent = 'No chapters selected';
        } else if (count === 1) {
            countText.textContent = '1 chapter selected';
        } else {
            countText.textContent = `${count} chapters selected`;
        }
    }
}

/**
 * Get selected chapter IDs for batch processing
 * Exposed for use by other scripts or HTMX
 */
function getSelectedChapterIds() {
    return Array.from(selectedChapterIds);
}

/**
 * Get current chapter data
 * Exposed for use by other scripts or HTMX
 */
function getCurrentChapterData() {
    const dataElement = document.getElementById('reading-view-data');
    if (!dataElement) return null;

    return {
        bookId: dataElement.dataset.bookId,
        chapterId: dataElement.dataset.chapterId,
        chapterNumber: dataElement.dataset.chapterNumber
    };
}

// Expose functions for external use
window.VoxLibriReading = {
    getSelectedChapterIds,
    getCurrentChapterData,
    selectedChapterIds
};
