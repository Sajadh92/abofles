// Abo Fles site: copy buttons, mobile menu, tabs, reveal on scroll, changelog filter. No dependencies.
document.documentElement.classList.remove('no-js')

// Copy: a button copies the text of its [data-copy] target (or its own data-text)
document.addEventListener('click', async e => {
  const btn = e.target.closest('.copy')
  if (!btn) return
  const src = btn.dataset.text ?? btn.closest('.cmd, pre')?.querySelector('code')?.innerText ?? ''
  const label = btn.querySelector('span')
  try {
    await navigator.clipboard.writeText(src.trim())
    btn.classList.add('done')
    if (label) label.textContent = btn.dataset.done || 'Copied'
  } catch {
    if (label) label.textContent = 'Select & copy'
  }
  setTimeout(() => { btn.classList.remove('done'); if (label) label.textContent = btn.dataset.label || 'Copy' }, 1800)
})

// Mobile menu
const menuBtn = document.querySelector('.menu-btn')
const nav = document.getElementById('nav')
if (menuBtn && nav) {
  menuBtn.addEventListener('click', () => {
    const open = nav.classList.toggle('open')
    menuBtn.setAttribute('aria-expanded', String(open))
  })
  nav.addEventListener('click', e => { if (e.target.closest('a')) { nav.classList.remove('open'); menuBtn.setAttribute('aria-expanded', 'false') } })
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && nav.classList.contains('open')) { nav.classList.remove('open'); menuBtn.setAttribute('aria-expanded', 'false'); menuBtn.focus() } })
}

// Tabs (WAI-ARIA tabs pattern: arrows move, Home/End jump)
document.querySelectorAll('[role="tablist"]').forEach(list => {
  const tabs = [...list.querySelectorAll('[role="tab"]')]
  const select = tab => {
    tabs.forEach(t => {
      const on = t === tab
      t.setAttribute('aria-selected', String(on))
      t.tabIndex = on ? 0 : -1
      const panel = document.getElementById(t.getAttribute('aria-controls'))
      if (panel) panel.hidden = !on
    })
  }
  tabs.forEach((t, i) => {
    t.addEventListener('click', () => select(t))
    t.addEventListener('keydown', e => {
      const k = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[e.key]
      if (k === undefined) return
      e.preventDefault()
      const next = tabs[(k + tabs.length) % tabs.length]
      select(next); next.focus()
    })
  })
})

// Reveal on scroll
const io = 'IntersectionObserver' in window ? new IntersectionObserver(entries => {
  entries.forEach(en => { if (en.isIntersecting) { en.target.classList.add('in'); io.unobserve(en.target) } })
}, { rootMargin: '0px 0px -8% 0px' }) : null
document.querySelectorAll('.reveal').forEach(el => io ? io.observe(el) : el.classList.add('in'))

// Changelog filter
const filter = document.getElementById('filter')
if (filter) {
  const rels = [...document.querySelectorAll('.rel')]
  const count = document.getElementById('filter-count')
  filter.addEventListener('input', () => {
    const q = filter.value.trim().toLowerCase()
    let n = 0
    rels.forEach(r => { const hit = !q || r.textContent.toLowerCase().includes(q); r.hidden = !hit; if (hit) n++ })
    if (count) count.textContent = q ? `${n} of ${rels.length} versions match` : ''
  })
}
