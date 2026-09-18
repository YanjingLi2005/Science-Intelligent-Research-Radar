/* Research Radar Zotero plugin core (skeleton).
 *
 * Uses Zotero's plugin APIs. This is intentionally minimal but should load in
 * Zotero 7 with the matching manifest.
 */

"use strict";

var Zotero = Zotero;
var Services = Services;

Zotero.ResearchRadar = {
  _initialized: false,
  _menuItem: null,

  init({ id, version, rootURI }) {
    if (this._initialized) return;
    this._id = id;
    this._version = version;
    this._rootURI = rootURI;
    this._initialized = true;
    this._registerMenu();
  },

  destroy() {
    if (this._menuItem && this._menuItem.parentNode) {
      this._menuItem.parentNode.removeChild(this._menuItem);
    }
    this._menuItem = null;
    this._initialized = false;
  },

  _registerMenu() {
    const handler = () => this._importSelectedItem();
    const menu = Zotero.Integration && Zotero.Integration.Menu;

    // The exact Zotero API may vary by version; this is a skeleton to adapt.
    if (menu && typeof menu.register === "function") {
      menu.register({
        id: "research-radar-import-item",
        target: "item",
        label: "加入 Research Radar",
        onCommand: handler,
      });
      return;
    }

    // Legacy fallback: add a menuitem to Zotero's item context-menu popup.
    const win = Zotero.getMainWindow && Zotero.getMainWindow();
    const doc = win && win.document;
    const popup = doc && doc.getElementById("zotero-itemmenu");
    if (!popup || !doc.createXULElement) return;

    const menuItem = doc.createXULElement("menuitem");
    menuItem.id = "research-radar-import-item";
    menuItem.setAttribute("label", "加入 Research Radar");
    menuItem.addEventListener("command", handler);
    popup.appendChild(menuItem);
    this._menuItem = menuItem;
  },

  async _importSelectedItem() {
    const pane = Zotero.getActiveZoteroPane();
    const items = pane && pane.getSelectedItems();
    const item = items && items[0];
    if (!item) return;

    try {
      const result = await this.importItem(item);
      const title = (result && result.title) || item.getField("title") || "成功";
      this._notify(`已加入 Research Radar：${title}`);
    } catch (error) {
      this._notify(`加入 Research Radar 失败：${error.message || error}`);
    }
  },

  _notify(message) {
    if (typeof Zotero.alert === "function") {
      Zotero.alert(null, "Research Radar", message);
      return;
    }
    const win = Zotero.getMainWindow && Zotero.getMainWindow();
    if (win && typeof win.alert === "function") win.alert(message);
  },

  /**
   * Import a Zotero item into Research Radar.
   * @param {Zotero.Item} item
   */
  async importItem(item) {
    const prefs = Zotero.Prefs.get("extensions.zotero.researchRadar");
    const base = (prefs.get("apiBase") || "http://localhost:8501/api").replace(/\/+$/, "");
    const caseId = prefs.get("caseId") || "";

    if (!caseId) {
      throw new Error("Please set Research Radar Case ID in Zotero preferences.");
    }

    const doi = item.getField("DOI");
    const url = item.getField("url") || item.uri || "";
    const payload = { doi: doi || "", url: url };

    const response = await Zotero.HTTP.request(
      "POST",
      `${base}/cases/${encodeURIComponent(caseId)}/sources/import`,
      {
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        responseType: "json",
      }
    );

    if (!response.response) {
      throw new Error(`Research Radar import failed (HTTP ${response.status})`);
    }
    return response.response;
  },
};
