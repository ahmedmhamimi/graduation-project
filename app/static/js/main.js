// Tab Switching
function openTab(evt, tabId) {
    const contents = document.querySelectorAll('.tab-content');
    contents.forEach(content => content.classList.add('hidden'));

    const links = document.querySelectorAll('.tab-link');
    links.forEach(link => {
        link.classList.remove('border-sky-500', 'text-sky-600');
        link.classList.add('border-transparent', 'text-slate-500');
    });

    document.getElementById(tabId).classList.remove('hidden');

    evt.currentTarget.classList.remove('border-transparent', 'text-slate-500');
    evt.currentTarget.classList.add('border-sky-500', 'text-sky-600');
}

// Availability Toggling
document.addEventListener('DOMContentLoaded', () => {
    const cells = document.querySelectorAll('.availability-cell');
    cells.forEach(cell => {
        cell.addEventListener('click', () => {
            if (cell.classList.contains('cell-available')) {
                cell.classList.replace('cell-available', 'cell-unavailable');
                cell.innerText = "Unavailable";
            } else {
                cell.classList.replace('cell-unavailable', 'cell-available');
                cell.innerText = "Available";
            }
        });
    });

    // Show flash messages from URL params
    const params = new URLSearchParams(window.location.search);
    const success = params.get('success');
    const error = params.get('error');

    if (success) {
        showFlash(success, 'success');
        // Clean URL
        window.history.replaceState({}, '', window.location.pathname);
    }
    if (error) {
        showFlash(error, 'error');
        window.history.replaceState({}, '', window.location.pathname);
    }
});

function showFlash(message, type) {
    const div = document.createElement('div');
    div.className = type === 'success'
        ? 'fixed top-4 right-4 z-[100] p-4 bg-green-50 border border-green-200 text-green-700 text-sm rounded-lg shadow-lg max-w-sm'
        : 'fixed top-4 right-4 z-[100] p-4 bg-red-50 border border-red-200 text-red-700 text-sm rounded-lg shadow-lg max-w-sm';

    div.innerHTML = `
        <div class="flex justify-between items-start gap-2">
            <span>${type === 'success' ? '✅' : '⚠️'} ${message}</span>
            <button onclick="this.parentElement.parentElement.remove()" class="text-lg leading-none opacity-50 hover:opacity-100">&times;</button>
        </div>
    `;
    document.body.appendChild(div);

    setTimeout(() => {
        if (div.parentElement) div.remove();
    }, 5000);
}