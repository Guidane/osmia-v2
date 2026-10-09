// Image galleries ({% image_gallery %}): upload on pick or drop, and a lightbox
// with previous/next, captions, "make cover" and remove. PDFs open in the
// lightbox too; other files show their name and a Download button.
(function () {
  if (window.OsmiaGallery) { window.OsmiaGallery.init(); return; }

  function setup(gallery) {
    if (gallery.dataset.ready) return;
    gallery.dataset.ready = '1';
    const upload = gallery.querySelector('.gallery-upload');
    const input = upload && upload.querySelector('input[type=file]');
    if (input) {
      input.addEventListener('change', () => { if (input.files.length) upload.submit(); });
      // Drop image files anywhere on the gallery card to add them.
      gallery.addEventListener('dragover', e => {
        if ([...e.dataTransfer.types].includes('Files')) { e.preventDefault(); gallery.classList.add('dropping'); }
      });
      gallery.addEventListener('dragleave', e => { if (!gallery.contains(e.relatedTarget)) gallery.classList.remove('dropping'); });
      gallery.addEventListener('drop', e => {
        gallery.classList.remove('dropping');
        if (!e.dataTransfer.files.length) return;
        e.preventDefault();
        input.files = e.dataTransfer.files;
        upload.submit();
      });
    }

    const items = [...gallery.querySelectorAll('.gallery-item')];
    const box = gallery.querySelector('.lightbox');
    if (!items.length || !box) return;
    const img = box.querySelector('.lightbox-stage img');
    const doc = box.querySelector('.lightbox-doc');
    const file = box.querySelector('.lightbox-file');
    const download = box.querySelector('.lightbox-download');
    const cover = box.querySelector('.lightbox-cover');
    const count = box.querySelector('.lightbox-count');
    const captionInput = box.querySelector('.lightbox-caption input[name=caption]');
    const captionText = box.querySelector('.lightbox-caption-text');
    let index = 0;

    function show(i) {
      index = (i + items.length) % items.length;
      const item = items[index];
      const kind = item.dataset.kind || 'image';
      // Pictures show as an image, PDFs in the browser's viewer, other files as a name to download.
      img.hidden = kind !== 'image';
      doc.hidden = kind !== 'pdf';
      file.hidden = kind !== 'file';
      if (kind === 'image') img.src = item.dataset.src; else img.removeAttribute('src');
      if (kind === 'pdf') doc.src = item.dataset.src; else doc.removeAttribute('src');
      if (kind === 'file') {
        file.querySelector('.file-ext').textContent = item.querySelector('.file-ext').textContent;
        file.querySelector('.file-name').textContent = item.dataset.name || '';
      }
      download.href = item.dataset.src + (item.dataset.src.includes('?') ? '&' : '?') + 'download=1';
      download.hidden = kind === 'image';
      if (cover) cover.hidden = kind !== 'image';
      img.alt = item.dataset.caption || '';
      count.textContent = `${index + 1} / ${items.length}`;
      if (captionInput) captionInput.value = item.dataset.caption || '';
      if (captionText) captionText.textContent = item.dataset.caption || '';
      box.querySelectorAll('.lightbox-form').forEach(f => { f.action = item.dataset.update; });
      box.querySelectorAll('.lightbox-nav').forEach(b => { b.hidden = items.length < 2; });
    }
    items.forEach((item, i) => item.addEventListener('click', () => { show(i); box.showModal(); }));
    box.querySelector('.prev').addEventListener('click', () => show(index - 1));
    box.querySelector('.next').addEventListener('click', () => show(index + 1));
    box.querySelector('.lightbox-close').addEventListener('click', () => box.close());
    box.addEventListener('close', () => doc.removeAttribute('src'));  // stop loading a closed PDF
    box.addEventListener('click', e => { if (e.target === box) box.close(); });  // click the backdrop
    box.addEventListener('keydown', e => {
      if (e.target.tagName === 'INPUT') return;
      if (e.key === 'ArrowLeft') show(index - 1);
      if (e.key === 'ArrowRight') show(index + 1);
    });
    box.querySelectorAll('[data-confirm]').forEach(b => b.addEventListener('click', e => {
      if (!window.confirm(b.dataset.confirm)) e.preventDefault();
    }));
  }

  window.OsmiaGallery = { init: () => document.querySelectorAll('[data-gallery]').forEach(setup) };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', window.OsmiaGallery.init);
  else window.OsmiaGallery.init();
})();
