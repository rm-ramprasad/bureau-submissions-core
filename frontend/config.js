// Local development: no sign-in (the API runs with SUBMISSIONS_AUTH=none) and the API on the same origin.
// On Azure the deploy step replaces this file with the Entra ID and Function App settings, e.g.
// window.APP_CONFIG = { auth: "entra", tenantId: "<directory (tenant) id>", clientId: "<application (client) id>",
//                       apiScope: "api://<application (client) id>/access_as_user",
//                       apiBase: "https://<function app>.azurewebsites.net" };
window.APP_CONFIG = { auth: "none" };
