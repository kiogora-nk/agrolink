async function loadChiefDashboard() {
    const token = localStorage.getItem('token');
    if (!token) { window.location.href = '/login'; return; }
    const headers = { 'Authorization': 'Bearer ' + token };

    // KPI cards
    const statsRes = await fetch('/api/chief/stats', { headers });
    const stats = await statsRes.json();
    document.getElementById('kpi-users').textContent = stats.total_users;
    document.getElementById('kpi-orders').textContent = stats.total_orders;
    document.getElementById('kpi-revenue').textContent = stats.revenue.toLocaleString();
    document.getElementById('kpi-products').textContent = stats.products;
    document.getElementById('kpi-chatbot').textContent = stats.chatbot_queries;
    document.getElementById('kpi-disease').textContent = stats.disease_scans;

    // User signup trend
    const signupsRes = await fetch('/api/chief/user-signups', { headers });
    const signups = await signupsRes.json();
    new Chart(document.getElementById('signupChart'), {
        type: 'line',
        data: { labels: signups.labels, datasets: [{ label: 'New Users', data: signups.data, borderColor: '#f59e0b' }] }
    });

    // Revenue trend
    const revRes = await fetch('/api/chief/revenue-trend', { headers });
    const rev = await revRes.json();
    new Chart(document.getElementById('revenueChart'), {
        type: 'bar',
        data: { labels: rev.labels, datasets: [{ label: 'Revenue (KES)', data: rev.data, backgroundColor: '#d97706' }] }
    });

    // Order status distribution
    const statusRes = await fetch('/api/chief/order-status', { headers });
    const statusData = await statusRes.json();
    new Chart(document.getElementById('orderStatusChart'), {
        type: 'doughnut',
        data: { labels: Object.keys(statusData), datasets: [{ data: Object.values(statusData), backgroundColor: ['#f59e0b','#fbbf24','#3b82f6','#ef4444'] }] }
    });

    // Top products
    const topRes = await fetch('/api/chief/top-products', { headers });
    const top = await topRes.json();
    new Chart(document.getElementById('topProductsChart'), {
        type: 'bar',
        data: { labels: top.labels, datasets: [{ label: 'Quantity Sold', data: top.data, backgroundColor: '#7c3aed' }] },
        options: { indexAxis: 'y' }
    });

    // User management
    const userRes = await fetch('/api/chief/users', { headers });
    const users = await userRes.json();
    const userDiv = document.getElementById('user-list');
    userDiv.innerHTML = '<ul class="space-y-2">';
    users.forEach(u => {
        userDiv.innerHTML += `<li class="flex justify-between items-center bg-gray-50 p-2 rounded">
            <span>${u.username} (${u.role}) ${u.suspended ? '(Suspended)' : '(Active)'}</span>
            <div>
                <button onclick="suspendUser(${u.id})" class="bg-red-100 px-2 py-1 text-xs rounded">${u.suspended ? 'Unsuspend' : 'Suspend'}</button>
                <button onclick="changeRole(${u.id}, '${u.role}')" class="bg-blue-100 px-2 py-1 text-xs rounded">Change Role</button>
            </div>
        </li>`;
    });
    userDiv.innerHTML += '</ul>';

    // Audit logs
    const auditRes = await fetch('/api/chief/recent-audit', { headers });
    const audits = await auditRes.json();
    const auditTable = document.getElementById('audit-table');
    audits.forEach(a => {
        auditTable.innerHTML += `<tr><td>${a.action}</td><td>${a.ip}</td><td>${a.timestamp}</td></tr>`;
    });
}

async function suspendUser(id) {
    const token = localStorage.getItem('token');
    const res = await fetch(`/api/chief/user/${id}/suspend`, {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' }
    });
    if (res.ok) location.reload();
}

async function changeRole(id, currentRole) {
    const newRole = prompt('Enter new role (buyer, farmer, admin, chief_admin):', currentRole);
    if (newRole) {
        const token = localStorage.getItem('token');
        await fetch(`/api/chief/user/${id}/changerole`, {
            method: 'POST',
            headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
            body: JSON.stringify({ role: newRole })
        });
        location.reload();
    }
}