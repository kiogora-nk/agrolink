// Reviews loader for product page
function loadReviews(productId, page = 1) {
    fetch(`/api/product/${productId}/reviews?page=${page}`)
        .then(res => res.json())
        .then(data => {
            const container = document.getElementById('reviews-list');
            if (!container) return;
            if (data.total_reviews === 0) {
                container.innerHTML = '<p class="text-gray-500">No reviews yet. Be the first!</p>';
                return;
            }
            let html = '';
            data.reviews.forEach(r => {
                html += `<div class="border-b pb-3"><div class="flex justify-between"><strong>${r.username}</strong><span>${'★'.repeat(r.rating)}${'☆'.repeat(5-r.rating)}</span></div><p>${r.comment}</p><small>${r.created_at}</small></div>`;
            });
            container.innerHTML = html;
            const paginationDiv = document.getElementById('reviews-pagination');
            if (data.has_next) {
                paginationDiv.innerHTML = `<button onclick="loadReviews(${productId}, ${page+1})" class="text-green-600">Load more</button>`;
            } else {
                paginationDiv.innerHTML = '';
            }
        });
}