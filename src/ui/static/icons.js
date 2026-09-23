/* Original line icons, bundled locally. */
const READER_ICONS = {
  book:'<path d="M12 5c-3-2-6-2-9-1v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1Z"/><path d="M12 5v15"/>',
  file:'<path d="M14 3H5v18h14V8Z"/><path d="M14 3v5h5M8 12h8M8 16h6"/>',
  upload:'<path d="M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5"/>',
  export:'<path d="M14 3h7v7m0-7L10 14M10 4H4v16h16v-6"/>',
  edit:'<path d="m15 4 5 5M4 20l5-1L21 7l-5-5L4 14Z"/>',
  chat:'<path d="M4 4h16v13H9l-5 4Z"/>',
  menu:'<path d="M4 6h16M4 12h16M4 18h16"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',
  arrow:'<path d="M4 12h16m-6-6 6 6-6 6"/>',
  folder:'<path d="M3 5h7l2 3h9v12H3Z"/>',
  settings:'<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="8" cy="6" r="2"/><circle cx="16" cy="12" r="2"/><circle cx="10" cy="18" r="2"/>',
  info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/>',
};
function readerIcon(name) {
  return `<svg class="reader-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${READER_ICONS[name]||READER_ICONS.file}</svg>`;
}
function hydrateReaderIcons(root=document) {
  root.querySelectorAll('[data-icon]').forEach(node=>{
    node.innerHTML=readerIcon(node.dataset.icon);
    node.removeAttribute('data-icon');
  });
}
