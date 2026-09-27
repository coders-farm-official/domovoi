/* Home — the dashboard's default page (#home).
 *
 * PLACEHOLDER. The shell work that makes Home the default route is done:
 * the route is reserved (index.html's DomovoiCore.pages, the core's
 * CORE_WEB_ROUTES, plugin_host's CORE_NAV), the brand row and the topbar
 * crumb link here, the phone strip keeps only the five primary tabs, and
 * the unknown-route fallback lands here. The real page — status line,
 * needs attention, timers, rooms, announce, today — replaces this file
 * (design-notes HOME-PLAN.md).
 *
 * Until then it carries the one part the phone shell already depends on:
 * the "everything" grid, which is the phone's way to every page that is
 * not one of the five tabs (plugin pages included).
 *
 * Every top-level name in this file starts with Home: all the dashboard's
 * scripts share one Babel scope, and a second `const Foo` anywhere is a
 * SyntaxError that kills whichever file loads later.
 */

/* Pages the "everything" grid lists besides the nav items: Settings has
 * no nav row (the topbar gear opens it) and the manual is reached from
 * Settings → About, so a phone would otherwise have no way to either. */
const HOME_EXTRA_TILES = [
  { route: 'settings', icon: 'settings', label: 'Settings', core: true },
  { route: 'manual', icon: 'book', label: 'User Manual', core: true },
];

const HomeEverything = ({ counts }) => {
  const manifest = window.DomovoiPluginManifest || { plugins: [] };
  const tiles = buildNavItems(manifest).filter((it) => !it.primary).concat(HOME_EXTRA_TILES);
  return (
    <Card title="everything">
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(96px, 1fr))',
                    gap: 8, padding: 12 }}>
        {tiles.map((it) => {
          const badge = it.core && it.countKey && counts ? counts[it.countKey] : null;
          return (
            <a key={it.route} href={`#${it.route}`}
               style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6,
                        padding: '12px 4px', borderRadius: 'var(--r-sm)',
                        border: '1px solid var(--border)', background: 'var(--card)',
                        color: 'var(--fg-muted)', textDecoration: 'none', fontSize: 12 }}>
              {it.core ? <Icon name={it.icon} size={18}/> : <PluginNavIcon src={it.iconSrc}/>}
              <span>{it.label}</span>
              {badge != null && <span className="mono" style={{ fontSize: 10, color: 'var(--fg-faint)' }}>{badge}</span>}
            </a>
          );
        })}
      </div>
    </Card>
  );
};

const HomePage = ({ counts }) => (
  <div className="page">
    <PageHeader title="Home" sub="the house at a glance"/>
    <HomeEverything counts={counts}/>
  </div>
);

window.HomePage = HomePage;
