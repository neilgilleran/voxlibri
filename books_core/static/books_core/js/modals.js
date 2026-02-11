/**
 * VoxLibri Modal Management
 * Phase 6: Modals & Real-Time Progress
 *
 * Handles:
 * - Cost preview modal
 * - Batch preview & progress modals
 * - Version comparison modal
 * - HTMX integration for dynamic content
 */

/**
 * Modal Manager - handles opening, closing, and state management
 */
const ModalManager = {
    activeModal: null,

    /**
     * Open a modal by ID
     */
    open(modalId) {
        const modal = document.getElementById(modalId);
        if (!modal) {
            console.error(`Modal ${modalId} not found`);
            return;
        }

        modal.classList.add('active');
        this.activeModal = modal;
        document.body.style.overflow = 'hidden';

        // Set up close handlers
        this.setupCloseHandlers(modal);
    },

    /**
     * Close the active modal
     */
    close(modalId = null) {
        const modal = modalId ? document.getElementById(modalId) : this.activeModal;
        if (!modal) return;

        modal.classList.remove('active');
        this.activeModal = null;
        document.body.style.overflow = '';
    },

    /**
     * Set up close handlers for a modal
     */
    setupCloseHandlers(modal) {
        // Close button
        const closeBtn = modal.querySelector('.modal-close');
        if (closeBtn) {
            closeBtn.onclick = () => this.close();
        }

        // Cancel button
        const cancelBtn = modal.querySelector('[data-modal-cancel]');
        if (cancelBtn) {
            cancelBtn.onclick = () => this.close();
        }

        // Overlay click (close if clicking outside modal)
        modal.onclick = (e) => {
            if (e.target === modal) {
                this.close();
            }
        };

        // Escape key
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.activeModal) {
                this.close();
            }
        });
    },

    /**
     * Update modal content
     */
    updateContent(modalId, content) {
        const modal = document.getElementById(modalId);
        if (!modal) return;

        const body = modal.querySelector('.modal-body');
        if (body) {
            body.innerHTML = content;
        }
    }
};

/**
 * Cost Preview Modal Handler
 */
const CostPreviewModal = {
    /**
     * Show cost preview for single chapter summary
     */
    async show(chapterId, promptId, model) {
        const data = window.VoxLibriReading.getCurrentChapterData();
        if (!data || !promptId) {
            console.error('Missing chapter or prompt data');
            return;
        }

        // Show loading state
        ModalManager.open('cost-preview-modal');
        this.showLoading();

        try {
            // Fetch cost preview from API
            const response = await fetch(`/api/chapters/${chapterId}/summary-preview/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    prompt_id: promptId,
                    model: model || 'gpt-4o-mini'
                })
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Failed to fetch cost preview');
            }

            const costData = await response.json();
            this.renderPreview(costData);

        } catch (error) {
            console.error('Cost preview error:', error);
            this.showError(error.message);
        }
    },

    /**
     * Show loading state
     */
    showLoading() {
        const content = `
            <div class="modal-loading">
                <div class="spinner"></div>
                <p class="loading-text">Calculating cost estimate...</p>
            </div>
        `;
        ModalManager.updateContent('cost-preview-modal', content);
    },

    /**
     * Render cost preview data
     */
    renderPreview(data) {
        const usagePercent = (data.monthly_usage.current / data.monthly_usage.limit) * 100;
        const dailyUsagePercent = (data.daily_usage.current_count / data.daily_usage.limit_count) * 100;

        const progressBarClass = usagePercent >= 90 ? 'danger' : (usagePercent >= 80 ? 'warning' : '');
        const dailyProgressBarClass = dailyUsagePercent >= 90 ? 'danger' : (dailyUsagePercent >= 80 ? 'warning' : '');

        let warningsHtml = '';
        if (data.warnings && data.warnings.length > 0) {
            warningsHtml = '<div class="warning-messages">';
            data.warnings.forEach(warning => {
                const alertClass = warning.includes('exceeded') ? 'danger' : 'warning';
                warningsHtml += `<div class="warning-alert ${alertClass}">${warning}</div>`;
            });
            warningsHtml += '</div>';
        }

        const content = `
            <div class="cost-breakdown">
                <div class="cost-breakdown-item">
                    <span class="cost-breakdown-label">Chapter</span>
                    <span class="cost-breakdown-value">${data.chapter_title}</span>
                </div>
                <div class="cost-breakdown-item">
                    <span class="cost-breakdown-label">Prompt</span>
                    <span class="cost-breakdown-value">${data.prompt_name}</span>
                </div>
                <div class="cost-breakdown-item">
                    <span class="cost-breakdown-label">Model</span>
                    <span class="cost-breakdown-value">${data.model}</span>
                </div>
                <div class="cost-breakdown-item">
                    <span class="cost-breakdown-label">Estimated Tokens</span>
                    <span class="cost-breakdown-value">${data.estimated_tokens.toLocaleString()}</span>
                </div>
                <div class="cost-breakdown-item">
                    <span class="cost-breakdown-label">Estimated Cost</span>
                    <span class="cost-breakdown-value cost-amount">$${data.estimated_cost_usd}</span>
                </div>
            </div>

            ${warningsHtml}

            <div class="usage-stats">
                <div class="usage-stat-card">
                    <div class="usage-stat-header">
                        <span class="usage-stat-label">Daily Usage</span>
                        <span class="usage-stat-value">${data.daily_usage.current_count} / ${data.daily_usage.limit_count}</span>
                    </div>
                    <div class="usage-progress">
                        <div class="usage-progress-bar ${dailyProgressBarClass}" style="width: ${Math.min(dailyUsagePercent, 100)}%"></div>
                    </div>
                    <div class="usage-remaining">${data.daily_usage.limit_count - data.daily_usage.current_count} summaries remaining today</div>
                </div>

                <div class="usage-stat-card">
                    <div class="usage-stat-header">
                        <span class="usage-stat-label">Monthly Budget</span>
                        <span class="usage-stat-value">$${data.monthly_usage.current} / $${data.monthly_usage.limit}</span>
                    </div>
                    <div class="usage-progress">
                        <div class="usage-progress-bar ${progressBarClass}" style="width: ${Math.min(usagePercent, 100)}%"></div>
                    </div>
                    <div class="usage-remaining">$${(data.monthly_usage.limit - data.monthly_usage.current).toFixed(4)} remaining this month</div>
                </div>
            </div>
        `;

        ModalManager.updateContent('cost-preview-modal', content);

        // Store data for confirmation
        this.pendingGeneration = {
            chapterId: data.chapter_id,
            promptId: data.prompt_name,
            model: data.model
        };
    },

    /**
     * Show error message
     */
    showError(message) {
        const content = `
            <div class="warning-alert danger">
                ${message}
            </div>
        `;
        ModalManager.updateContent('cost-preview-modal', content);
    },

    /**
     * Confirm and generate summary
     */
    async confirm() {
        if (!this.pendingGeneration) {
            console.error('No pending generation data');
            return;
        }

        const { chapterId, promptId, model } = this.pendingGeneration;
        ModalManager.close('cost-preview-modal');

        // Show generating state in summary panel
        const summaryDisplay = document.getElementById('summary-display');
        if (summaryDisplay) {
            summaryDisplay.innerHTML = `
                <div class="modal-loading">
                    <div class="spinner"></div>
                    <p class="loading-text">Generating summary...</p>
                </div>
            `;
        }

        try {
            // Get prompt ID from selector
            const promptSelector = document.getElementById('prompt-selector');
            const selectedPromptId = promptSelector ? promptSelector.value : null;

            console.log('Generating summary with:', { chapterId, promptId: selectedPromptId, model });

            // Call generation API
            const response = await fetch(`/api/chapters/${chapterId}/summary-generate/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    prompt_id: selectedPromptId,
                    model: model,
                    confirmed: true
                })
            });

            console.log('API Response status:', response.status);

            if (!response.ok) {
                const error = await response.json();
                console.error('API Error:', error);
                throw new Error(error.error || 'Failed to generate summary');
            }

            const summaryData = await response.json();
            console.log('Summary data received:', summaryData);

            this.displaySummary(summaryData);
            console.log('Summary displayed');

            // Update chapter indicator without reloading
            this.updateChapterIndicator(summaryData.chapter_id);

            // DON'T RELOAD - just keep the summary visible

        } catch (error) {
            console.error('Summary generation error:', error);
            console.error('Error stack:', error.stack);
            if (summaryDisplay) {
                summaryDisplay.innerHTML = `
                    <div class="warning-alert danger">
                        <strong>Error:</strong> ${error.message}
                    </div>
                `;
            }
        }
    },

    /**
     * Display generated summary
     */
    displaySummary(data) {
        const summaryDisplay = document.getElementById('summary-display');
        if (!summaryDisplay) return;

        const content = `
            <div class="summary-content">
                <div class="summary-metadata">
                    <div class="summary-metadata-item">
                        <span class="summary-metadata-label">Prompt:</span>
                        <span class="summary-metadata-value">${data.prompt_name || 'N/A'}</span>
                    </div>
                    <div class="summary-metadata-item">
                        <span class="summary-metadata-label">Version:</span>
                        <span class="summary-metadata-value">${data.version}</span>
                    </div>
                    <div class="summary-metadata-item">
                        <span class="summary-metadata-label">Cost:</span>
                        <span class="summary-metadata-value">$${data.cost_usd}</span>
                    </div>
                    <div class="summary-metadata-item">
                        <span class="summary-metadata-label">Tokens:</span>
                        <span class="summary-metadata-value">${data.tokens_used.toLocaleString()}</span>
                    </div>
                </div>
                <div class="summary-text">
                    ${data.content_markdown || data.content}
                </div>
            </div>
        `;

        summaryDisplay.innerHTML = content;
    },

    /**
     * Update chapter indicator in navigation
     */
    updateChapterIndicator(chapterId) {
        // Find the chapter link and add summary indicator
        const chapterCheckbox = document.querySelector(`.chapter-checkbox[data-chapter-id="${chapterId}"]`);
        if (chapterCheckbox) {
            const listItem = chapterCheckbox.closest('.chapter-list-item');
            const chapterLink = listItem.querySelector('.chapter-link');

            // Check if indicator already exists
            if (chapterLink && !chapterLink.querySelector('.summary-indicator')) {
                const indicator = document.createElement('span');
                indicator.className = 'summary-indicator';
                indicator.title = 'Has summaries';
                indicator.textContent = '✓';
                chapterLink.appendChild(indicator);
            }
        }
    },

    /**
     * Load available versions for this chapter
     */
    async loadVersions(chapterId) {
        try {
            const response = await fetch(`/api/chapters/${chapterId}/summaries/`);
            if (!response.ok) return;

            const data = await response.json();
            const versionSelector = document.getElementById('version-selector');
            const versionGroup = document.getElementById('version-selector-group');
            const compareBtn = document.getElementById('compare-versions');

            if (data.summaries && data.summaries.length > 0) {
                // Show version selector
                if (versionGroup) versionGroup.style.display = 'block';
                if (compareBtn && data.summaries.length > 1) compareBtn.style.display = 'block';

                // Populate version dropdown
                if (versionSelector) {
                    versionSelector.innerHTML = data.summaries.map(summary => `
                        <option value="${summary.id}">
                            v${summary.version} - ${summary.prompt_name} (${new Date(summary.created_at).toLocaleDateString()})
                        </option>
                    `).join('');

                    // Add change listener to load selected version
                    versionSelector.addEventListener('change', async function() {
                        const summaryId = this.value;
                        if (!summaryId) return;

                        try {
                            const resp = await fetch(`/api/summaries/${summaryId}/`);
                            if (resp.ok) {
                                const summaryData = await resp.json();
                                CostPreviewModal.displaySummary(summaryData);
                            }
                        } catch (error) {
                            console.error('Failed to load version:', error);
                        }
                    });
                }
            }
        } catch (error) {
            console.error('Failed to load versions:', error);
        }
    }
};

/**
 * Batch Preview & Progress Modal Handler
 */
const BatchModal = {
    /**
     * Show batch preview
     */
    async showPreview() {
        const selectedIds = window.VoxLibriReading.getSelectedChapterIds();
        if (!selectedIds || selectedIds.length < 2) {
            alert('Please select at least 2 chapters for batch processing.');
            return;
        }

        const promptSelector = document.getElementById('prompt-selector');
        const modelSelector = document.getElementById('model-selector');

        if (!promptSelector || !promptSelector.value) {
            alert('Please select a prompt first.');
            return;
        }

        const promptId = promptSelector.value;
        const model = modelSelector ? modelSelector.value : 'gpt-4o-mini';

        // Show modal with loading
        ModalManager.open('batch-preview-modal');
        this.showLoading();

        try {
            const response = await fetch('/api/summaries/batch-preview/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    chapter_ids: selectedIds,
                    prompt_id: promptId,
                    model: model
                })
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Failed to fetch batch preview');
            }

            const batchData = await response.json();
            this.renderPreview(batchData);

        } catch (error) {
            console.error('Batch preview error:', error);
            this.showError(error.message);
        }
    },

    /**
     * Show loading state
     */
    showLoading() {
        const content = `
            <div class="modal-loading">
                <div class="spinner"></div>
                <p class="loading-text">Calculating batch cost...</p>
            </div>
        `;
        ModalManager.updateContent('batch-preview-modal', content);
    },

    /**
     * Render batch preview
     */
    renderPreview(data) {
        const usagePercent = (data.monthly_usage.current / data.monthly_usage.limit) * 100;
        const progressBarClass = usagePercent >= 90 ? 'danger' : (usagePercent >= 80 ? 'warning' : '');

        let warningsHtml = '';
        if (data.warnings && data.warnings.length > 0) {
            warningsHtml = '<div class="warning-messages">';
            data.warnings.forEach(warning => {
                const alertClass = warning.includes('exceeded') ? 'danger' : 'warning';
                warningsHtml += `<div class="warning-alert ${alertClass}">${warning}</div>`;
            });
            warningsHtml += '</div>';
        }

        let chaptersHtml = '<div class="batch-chapters-list">';
        data.chapters.forEach(chapter => {
            chaptersHtml += `
                <div class="batch-chapter-item">
                    <span class="batch-chapter-number">Ch ${chapter.chapter_number}</span>
                    <span class="batch-chapter-title">${chapter.title}</span>
                    <span class="batch-chapter-cost">$${chapter.estimated_cost_usd}</span>
                </div>
            `;
        });
        chaptersHtml += '</div>';

        const content = `
            <div class="batch-summary">
                <div class="batch-summary-item">
                    <span class="batch-summary-label">Chapters Selected:</span>
                    <span class="batch-summary-value">${data.chapters.length}</span>
                </div>
                <div class="batch-summary-item">
                    <span class="batch-summary-label">Total Tokens:</span>
                    <span class="batch-summary-value">${data.total_tokens.toLocaleString()}</span>
                </div>
                <div class="batch-summary-item">
                    <span class="batch-summary-label">Total Cost:</span>
                    <span class="batch-summary-value cost-amount">$${data.total_cost_usd}</span>
                </div>
            </div>

            ${warningsHtml}

            ${chaptersHtml}

            <div class="usage-stats">
                <div class="usage-stat-card">
                    <div class="usage-stat-header">
                        <span class="usage-stat-label">Monthly Budget</span>
                        <span class="usage-stat-value">$${data.monthly_usage.current} / $${data.monthly_usage.limit}</span>
                    </div>
                    <div class="usage-progress">
                        <div class="usage-progress-bar ${progressBarClass}" style="width: ${Math.min(usagePercent, 100)}%"></div>
                    </div>
                </div>
            </div>
        `;

        ModalManager.updateContent('batch-preview-modal', content);

        // Store data for confirmation
        this.pendingBatch = data;
    },

    /**
     * Show error
     */
    showError(message) {
        const content = `
            <div class="warning-alert danger">
                ${message}
            </div>
        `;
        ModalManager.updateContent('batch-preview-modal', content);
    },

    /**
     * Confirm and start batch processing
     */
    async confirmBatch() {
        if (!this.pendingBatch) {
            console.error('No pending batch data');
            return;
        }

        const selectedIds = window.VoxLibriReading.getSelectedChapterIds();
        const promptSelector = document.getElementById('prompt-selector');
        const modelSelector = document.getElementById('model-selector');

        const promptId = promptSelector.value;
        const model = modelSelector ? modelSelector.value : 'gpt-4o-mini';

        ModalManager.close('batch-preview-modal');
        ModalManager.open('batch-progress-modal');

        try {
            const response = await fetch('/api/summaries/batch-generate/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    chapter_ids: selectedIds,
                    prompt_id: promptId,
                    model: model,
                    confirmed: true
                })
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Failed to start batch processing');
            }

            const jobData = await response.json();
            this.startProgressTracking(jobData.job_id);

        } catch (error) {
            console.error('Batch generation error:', error);
            ModalManager.updateContent('batch-progress-modal', `
                <div class="warning-alert danger">
                    Error: ${error.message}
                </div>
            `);
        }
    },

    /**
     * Start WebSocket progress tracking
     */
    startProgressTracking(jobId) {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/batch/${jobId}/`;

        const socket = new WebSocket(wsUrl);

        socket.onopen = () => {
            console.log('WebSocket connected for batch job:', jobId);
            this.initializeProgressDisplay();
        };

        socket.onmessage = (event) => {
            const data = JSON.parse(event.data);
            this.updateProgress(data);
        };

        socket.onerror = (error) => {
            console.error('WebSocket error:', error);
        };

        socket.onclose = () => {
            console.log('WebSocket closed');
        };

        this.socket = socket;
    },

    /**
     * Initialize progress display
     */
    initializeProgressDisplay() {
        const selectedIds = window.VoxLibriReading.getSelectedChapterIds();

        let itemsHtml = '<div class="progress-items-list">';
        selectedIds.forEach(id => {
            itemsHtml += `
                <div class="progress-item" data-chapter-id="${id}">
                    <div class="progress-item-status pending">⋯</div>
                    <div class="progress-item-chapter">
                        <div class="progress-item-title">Chapter ${id}</div>
                        <div class="progress-item-message">Pending...</div>
                    </div>
                </div>
            `;
        });
        itemsHtml += '</div>';

        const content = `
            <div class="progress-container">
                <div class="progress-header">
                    <span class="progress-label">Overall Progress</span>
                    <span class="progress-percentage">0%</span>
                </div>
                <div class="progress-bar-container">
                    <div class="progress-bar" style="width: 0%"></div>
                </div>
            </div>
            ${itemsHtml}
        `;

        ModalManager.updateContent('batch-progress-modal', content);
    },

    /**
     * Update progress from WebSocket message
     */
    updateProgress(data) {
        // Update overall progress
        const progressBar = document.querySelector('.progress-bar');
        const progressPercentage = document.querySelector('.progress-percentage');

        if (progressBar && data.progress !== undefined) {
            progressBar.style.width = `${data.progress}%`;
            if (progressPercentage) {
                progressPercentage.textContent = `${Math.round(data.progress)}%`;
            }
            if (data.progress >= 100) {
                progressBar.classList.add('completed');
            }
        }

        // Update individual chapter item
        if (data.chapter_id) {
            const item = document.querySelector(`[data-chapter-id="${data.chapter_id}"]`);
            if (item) {
                const status = item.querySelector('.progress-item-status');
                const message = item.querySelector('.progress-item-message');

                if (status) {
                    status.className = `progress-item-status ${data.status}`;
                    if (data.status === 'success') {
                        status.textContent = '✓';
                    } else if (data.status === 'error') {
                        status.textContent = '✗';
                    } else if (data.status === 'processing') {
                        status.textContent = '⋯';
                    }
                }

                if (message) {
                    message.textContent = data.message || '';
                    if (data.status === 'error') {
                        message.classList.add('error');
                    }
                }
            }
        }

        // Check if complete
        if (data.type === 'complete') {
            setTimeout(() => {
                if (this.socket) {
                    this.socket.close();
                }
                // Reload page after delay to show updated summaries
                setTimeout(() => {
                    window.location.reload();
                }, 2000);
            }, 1000);
        }
    }
};

/**
 * Version Comparison Modal Handler
 */
const VersionComparisonModal = {
    /**
     * Show version comparison
     */
    async show() {
        const versionSelector = document.getElementById('version-selector');
        if (!versionSelector || versionSelector.options.length < 2) {
            alert('At least 2 versions are required for comparison.');
            return;
        }

        ModalManager.open('version-comparison-modal');
        this.showLoading();

        // Get all version IDs
        const versionIds = Array.from(versionSelector.options)
            .map(opt => opt.value)
            .filter(v => v);

        if (versionIds.length < 2) {
            this.showError('At least 2 versions are required for comparison.');
            return;
        }

        try {
            // Fetch version data
            const versions = await Promise.all(
                versionIds.slice(0, 2).map(id =>
                    fetch(`/api/summaries/${id}/`).then(r => r.json())
                )
            );

            this.renderComparison(versions);

        } catch (error) {
            console.error('Version comparison error:', error);
            this.showError(error.message);
        }
    },

    /**
     * Show loading state
     */
    showLoading() {
        const content = `
            <div class="modal-loading">
                <div class="spinner"></div>
                <p class="loading-text">Loading versions...</p>
            </div>
        `;
        ModalManager.updateContent('version-comparison-modal', content);
    },

    /**
     * Render version comparison
     */
    renderComparison(versions) {
        const content = `
            <div class="comparison-container">
                ${versions.map(v => `
                    <div class="version-column">
                        <div class="version-header">
                            <div class="version-title">Version ${v.version}</div>
                            <div class="version-metadata">
                                ${new Date(v.created_at).toLocaleString()} • $${v.cost_usd} • ${v.tokens_used} tokens
                            </div>
                        </div>
                        <div class="version-content">
                            ${v.content_markdown || v.content}
                        </div>
                    </div>
                `).join('')}
            </div>
        `;

        ModalManager.updateContent('version-comparison-modal', content);
    },

    /**
     * Show error
     */
    showError(message) {
        const content = `
            <div class="warning-alert danger">
                ${message}
            </div>
        `;
        ModalManager.updateContent('version-comparison-modal', content);
    }
};

/**
 * Initialize modal handlers on page load
 */
document.addEventListener('DOMContentLoaded', function() {
    // Load existing summary for current chapter
    const data = window.VoxLibriReading ? window.VoxLibriReading.getCurrentChapterData() : null;
    if (data && data.chapterId) {
        loadExistingSummary(data.chapterId);
    }

    // Generate Summary button
    const generateBtn = document.getElementById('generate-summary');
    if (generateBtn) {
        generateBtn.addEventListener('click', function() {
            const promptSelector = document.getElementById('prompt-selector');
            const modelSelector = document.getElementById('model-selector');
            const data = window.VoxLibriReading.getCurrentChapterData();

            if (!promptSelector || !promptSelector.value) {
                alert('Please select a prompt first.');
                return;
            }

            if (!data || !data.chapterId) {
                alert('Chapter data not available.');
                return;
            }

            CostPreviewModal.show(
                data.chapterId,
                promptSelector.value,
                modelSelector ? modelSelector.value : 'gpt-4o-mini'
            );
        });
    }

    // Batch Generate button
    const batchBtn = document.getElementById('batch-generate');
    if (batchBtn) {
        batchBtn.addEventListener('click', function() {
            BatchModal.showPreview();
        });
    }

    // Compare Versions button
    const compareBtn = document.getElementById('compare-versions');
    if (compareBtn) {
        compareBtn.addEventListener('click', function() {
            VersionComparisonModal.show();
        });
    }

    // Load prompts via HTMX
    loadPromptsHTMX();
});

/**
 * Load existing summary for chapter on page load
 */
async function loadExistingSummary(chapterId) {
    console.log('Loading existing summary for chapter:', chapterId);
    try {
        const response = await fetch(`/api/chapters/${chapterId}/summaries/`);
        console.log('API response status:', response.status);

        if (!response.ok) {
            console.error('API response not OK:', response.status);
            return;
        }

        const data = await response.json();
        console.log('API returned data:', data);

        if (data.summaries && data.summaries.length > 0) {
            console.log('Found', data.summaries.length, 'summaries');
            // Get the most recent summary (first in the list)
            const latestSummary = data.summaries[0];

            // Fetch full summary details
            const summaryResponse = await fetch(`/api/summaries/${latestSummary.id}/`);
            console.log('Fetching summary details for ID:', latestSummary.id);

            if (summaryResponse.ok) {
                const summaryData = await summaryResponse.json();
                console.log('Summary data:', summaryData);

                // Display the summary FIRST
                CostPreviewModal.displaySummary(summaryData);
                console.log('Summary displayed!');

                // Then populate version selector WITHOUT triggering change events
                const versionGroup = document.getElementById('version-selector-group');
                const versionSelector = document.getElementById('version-selector');
                const compareBtn = document.getElementById('compare-versions');

                if (versionGroup) versionGroup.style.display = 'block';
                if (compareBtn && data.summaries.length > 1) compareBtn.style.display = 'block';

                if (versionSelector) {
                    // Remove any existing event listeners by cloning the element
                    const newVersionSelector = versionSelector.cloneNode(false);
                    versionSelector.parentNode.replaceChild(newVersionSelector, versionSelector);

                    // Populate dropdown
                    newVersionSelector.innerHTML = data.summaries.map((summary, index) => `
                        <option value="${summary.id}" ${index === 0 ? 'selected' : ''}>
                            v${summary.version} - ${summary.prompt_name} (${new Date(summary.created_at).toLocaleDateString()})
                        </option>
                    `).join('');

                    // Add change listener AFTER populating
                    newVersionSelector.addEventListener('change', async function() {
                        const summaryId = this.value;
                        if (!summaryId) return;

                        try {
                            const resp = await fetch(`/api/summaries/${summaryId}/`);
                            if (resp.ok) {
                                const versionData = await resp.json();
                                CostPreviewModal.displaySummary(versionData);
                            }
                        } catch (error) {
                            console.error('Failed to load version:', error);
                        }
                    });
                }
            }
        }
    } catch (error) {
        console.error('Failed to load existing summary:', error);
    }
}

/**
 * Load prompts using HTMX
 */
function loadPromptsHTMX() {
    const promptSelector = document.getElementById('prompt-selector');
    if (!promptSelector) return;

    // Fetch prompts
    fetch('/api/prompts/?is_fabric=true')
        .then(response => response.json())
        .then(data => {
            promptSelector.innerHTML = '<option value="">Select a prompt...</option>';
            data.prompts.forEach(prompt => {
                const option = document.createElement('option');
                option.value = prompt.id;
                option.textContent = prompt.name;
                promptSelector.appendChild(option);
            });
        })
        .catch(error => {
            console.error('Failed to load prompts:', error);
            promptSelector.innerHTML = '<option value="">Failed to load prompts</option>';
        });
}

// Expose to global scope
window.ModalManager = ModalManager;
window.CostPreviewModal = CostPreviewModal;
window.BatchModal = BatchModal;
window.VersionComparisonModal = VersionComparisonModal;
