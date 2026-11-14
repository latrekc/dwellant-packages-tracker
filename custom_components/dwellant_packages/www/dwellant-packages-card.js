/* Dwellant Packages card — dependency-free vanilla JS.
 * Shows available packages for a dwellant_packages sensor + collected history.
 * Config: { entity, show_collected=true }
 * Collection codes are internal tracking keys only and are never displayed.
 * Both tables show type + dates (with relative time, e.g. "5 hours ago").
 */
class DwellantPackagesCard extends HTMLElement {
  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("Dwellant Packages card: 'entity' is required");
    }
    this._config = {
      show_collected: true,
      ...config,
    };
  }

  set hass(hass) {
    const previous = this._hass;
    this._hass = hass;
    const id = this._config && this._config.entity;
    // HA pushes `hass` on every entity's change. Re-render only when our own
    // entity object actually changed, so unrelated updates don't keep wiping
    // the DOM (which was collapsing the expanded Collected section instantly).
    if (this._rendered && id && previous && previous.states[id] === hass.states[id]) {
      return;
    }
    this._render();
  }

  getCardSize() {
    return 3;
  }

  static getStubConfig() {
    return { entity: "", show_collected: true };
  }

  _render() {
    if (!this._hass || !this._config) return;
    // Preserve the expanded Collected section across re-renders.
    const openCollected = (() => {
      const d = this.querySelector("details.dp-collected");
      return d ? d.open : false;
    })();
    const state = this._hass.states[this._config.entity];
    if (!state) {
      this.innerHTML = `<ha-card header="Dwellant Packages"><div class="card-content">Entity not found: ${this._config.entity}</div></ha-card>`;
      this._rendered = true;
      return;
    }
    const attrs = state.attributes || {};
    const packages = attrs.packages || [];
    const collected = attrs.collected || [];
    const email = attrs.email || "";
    const count = state.state;
    const showDesc = [...packages, ...collected].some((p) => p.description);

    const rows = packages
      .map(
        (p) => `<tr>
          <td>${this._esc(p.type)}</td>
          <td>${this._esc(this._formatWhen(p.delivery_time, p.delivery_time_raw))}</td>
          ${showDesc ? `<td>${this._esc(p.description || "")}</td>` : ""}
        </tr>`
      )
      .join("");

    const collectedRows = collected
      .map(
        (p) => `<tr>
          <td>${this._esc(p.type)}</td>
          <td>${this._esc(this._formatWhen(p.delivery_time, p.delivery_time_raw))}</td>
          <td>${this._esc(this._formatWhen(p.collected_at, null))}</td>
          ${showDesc ? `<td>${this._esc(p.description || "")}</td>` : ""}
        </tr>`
      )
      .join("");

    const title = email ? `📦 ${this._esc(email)} (${count})` : `📦 Packages (${count})`;
    const descHeader = showDesc ? `<th>Note</th>` : "";
    this.innerHTML = `
      <ha-card header="${title}">
        <div class="card-content">
          ${
            packages.length
              ? `<table>
                  <thead><tr><th>Type</th><th>Delivered</th>${descHeader}</tr></thead>
                  <tbody>${rows}</tbody>
                </table>`
              : `<p class="empty">No packages waiting. 🎉</p>`
          }
          ${
            this._config.show_collected && collected.length
              ? `<details class="dp-collected">
                  <summary>Collected (${collected.length})</summary>
                  <table>
                    <thead><tr><th>Type</th><th>Delivered</th><th>Collected</th>${descHeader}</tr></thead>
                    <tbody>${collectedRows}</tbody>
                  </table>
                </details>`
              : ""
          }
        </div>
      </ha-card>
      <style>
        table { width: 100%; border-collapse: collapse; font-size: 0.9em; }
        th, td { text-align: left; padding: 6px 4px; border-bottom: 1px solid var(--divider-color); }
        .empty { opacity: 0.7; }
        details { margin-top: 12px; }
        summary { cursor: pointer; opacity: 0.8; }
      </style>`;
    if (openCollected) {
      const d = this.querySelector("details.dp-collected");
      if (d) d.open = true;
    }
    this._rendered = true;
  }

  _formatWhen(iso, raw) {
    // "18/09/2026 at 15:24 (5 hours ago)" — relative part omitted when unknown.
    const base = raw || iso || "";
    if (!iso) return base;
    const rel = this._relative(new Date(iso), new Date());
    return rel ? `${base} (${rel})` : base;
  }

  _relative(then, now) {
    const diffMs = now - then;
    if (Number.isNaN(diffMs)) return "";
    const future = diffMs < 0;
    const mins = Math.round(Math.abs(diffMs) / 60000);
    let text;
    if (mins < 1) text = "just now";
    else if (mins < 60) text = `${mins} min${mins === 1 ? "" : "s"}`;
    else {
      const hours = Math.round(mins / 60);
      if (hours < 24) text = `${hours} hour${hours === 1 ? "" : "s"}`;
      else {
        const days = Math.round(hours / 24);
        text = days < 7 ? `${days} day${days === 1 ? "" : "s"}` : `${Math.round(days / 7)} wk`;
      }
    }
    if (text === "just now") return text;
    return future ? `in ${text}` : `${text} ago`;
  }

  _esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
}

customElements.define("dwellant-packages-card", DwellantPackagesCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "dwellant-packages-card",
  name: "Dwellant Packages Card",
  description: "Per-user Dwellant parcel list (type + delivery time).",
});
