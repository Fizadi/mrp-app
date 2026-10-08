/* static/js/routing.js */
/* Pure JavaScript router utilities – NO Jinja allowed here */

(function () {

    const Router = {

        go: function (path) {
            if (!path) return;
            window.location.href = path;
        },

        reload: function () {
            window.location.reload();
        },

        back: function () {
            window.history.back();
        },

        open: function (path) {
            if (!path) return;
            window.open(path, "_blank");
        },

        api: function (endpoint) {
            if (!endpoint) return "";

            if (endpoint.startsWith("/")) {
                return endpoint;
            }

            return "/" + endpoint;
        },

        fetchJSON: async function (url, options = {}) {

            const defaultOptions = {
                headers: {
                    "Content-Type": "application/json"
                }
            };

            const finalOptions = Object.assign({}, defaultOptions, options);

            const response = await fetch(url, finalOptions);

            if (!response.ok) {
                throw new Error("HTTP error " + response.status);
            }

            return await response.json();
        },

        postJSON: async function (url, data) {

            return await this.fetchJSON(url, {
                method: "POST",
                body: JSON.stringify(data)
            });
        },

        putJSON: async function (url, data) {

            return await this.fetchJSON(url, {
                method: "PUT",
                body: JSON.stringify(data)
            });
        },

        deleteJSON: async function (url) {

            return await this.fetchJSON(url, {
                method: "DELETE"
            });
        }

    };

    window.AppRouter = Router;

})();
