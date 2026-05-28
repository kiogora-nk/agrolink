async function sendMessage() {
    const input = document.getElementById('user-input');
    const msg = input.value.trim();
    if (!msg) return;
    appendMessage('user', msg);
    input.value = '';
    const res = await fetch('/api/chatbot', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({message: msg})
    });
    const data = await res.json();
    appendMessage('bot', data.reply);
}

function appendMessage(sender, text) {
    const container = document.getElementById('chat-log');
    const div = document.createElement('div');
    div.className = `mb-2 p-2 rounded-lg ${sender === 'user' ? 'bg-blue-100 ml-10' : 'bg-gray-100 mr-10'}`;
    div.textContent = text;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}