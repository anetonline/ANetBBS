(() => {
    'use strict';
    const controls = document.getElementById('file-description-controls');
    const descriptions = Array.from(document.querySelectorAll('details.file-description-details'));
    if (!controls || descriptions.length === 0) return;

    const storageKey = 'anetbbs.fileAreas.descriptionView';
    const buttons = Array.from(controls.querySelectorAll('[data-description-mode]'));
    const syncPreview = detail => {
        const preview = detail.previousElementSibling;
        if (preview && preview.classList.contains('file-description-preview')) {
            preview.classList.toggle('d-none', detail.open);
        }
    };
    const applyMode = mode => {
        descriptions.forEach(detail => {
            detail.open = mode === 'full';
            syncPreview(detail);
        });
        buttons.forEach(button => {
            const active = button.dataset.descriptionMode === mode;
            button.classList.toggle('active', active);
            button.setAttribute('aria-pressed', String(active));
        });
    };
    let mode = 'compact';
    try {
        if (localStorage.getItem(storageKey) === 'full') mode = 'full';
    } catch (_) {
        // Storage can be blocked; the controls still work for this page.
    }
    descriptions.forEach(detail => {
        detail.addEventListener('toggle', () => syncPreview(detail));
    });
    buttons.forEach(button => {
        button.addEventListener('click', () => {
            mode = button.dataset.descriptionMode;
            applyMode(mode);
            try {
                localStorage.setItem(storageKey, mode);
            } catch (_) {
                // Keep the current view even when storage is unavailable.
            }
        });
    });
    applyMode(mode);
    controls.classList.remove('d-none');
})();
