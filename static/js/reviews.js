// Reviews loader for product page
const starSvg = (cls) => `<svg class="inline-block h-4 w-4 ${cls}" fill="currentColor" viewBox="0 0 20 20" aria-hidden="true"><path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.286 3.958a1 1 0 00.95.69h4.162c.969 0 1.371 1.24.588 1.81l-3.368 2.447a1 1 0 00-.363 1.118l1.286 3.958c.3.922-.755 1.688-1.539 1.118l-3.367-2.447a1 1 0 00-1.176 0l-3.367 2.447c-.783.57-1.838-.196-1.539-1.118l1.286-3.958a1 1 0 00-.363-1.118L2.982 9.385c-.783-.57-.38-1.81.588-1.81h4.162a1 1 0 00.951-.69l1.286-3.958z"/></svg>`;

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
                const stars = starSvg('text-amber-500').repeat(r.rating) +
                              starSvg('text-slate-200').repeat(5 - r.rating);
                html += `<div class="border-b pb-3"><div class="flex justify-between"><strong>${r.username}</strong><span>${stars}</span></div><p>${r.comment}</p><small>${r.created_at}</small></div>`;
            });
            container.innerHTML = html;
            const paginationDiv = document.getElementById('reviews-pagination');
            if (data.has_next) {
                paginationDiv.innerHTML = `<button onclick="loadReviews(${productId}, ${page+1})" class="text-amber-700">Load more</button>`;
            } else {
                paginationDiv.innerHTML = '';
            }
        });
}