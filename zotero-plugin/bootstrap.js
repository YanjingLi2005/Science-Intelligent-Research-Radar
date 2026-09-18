/* Research Radar Zotero plugin skeleton.
 *
 * This is a starting point for a full Zotero integration. It registers a
 * right-click menu on items and posts the item's DOI/URL to the local
 * Research Radar API.
 *
 * A production version should:
 *  - read Zotero prefs for API base URL and default Case ID
 *  - handle network errors and show progress
 *  - support multiple selected items
 *  - use Zotero.HTTP.request for requests
 */

function install() {}
function uninstall() {}

function startup({ id, version, rootURI }) {
  Services.scriptloader.loadSubScript(rootURI + 'zotero-plugin.js', this);
  Zotero.ResearchRadar.init({ id, version, rootURI });
}

function shutdown() {
  Zotero.ResearchRadar.destroy();
}
