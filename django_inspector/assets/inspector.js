/* django-inspector dashboard behaviour. Served by dashboard.views.asset.
   Event delegation instead of inline handlers, so the dashboard works under
   its Content-Security-Policy (script-src 'self'). */
(function () {
    "use strict";

    function togglePolling(button) {
        var target = document.getElementById(button.getAttribute("data-inspector-toggle-polling"));
        if (!target) {
            return;
        }
        if (target.getAttribute("hx-trigger")) {
            target.removeAttribute("hx-trigger");
            button.textContent = "▶ Resume";
            button.classList.add("inspector-btn--paused");
        } else {
            target.setAttribute("hx-trigger", "every 3s");
            button.textContent = "⏸ Pause";
            button.classList.remove("inspector-btn--paused");
        }
        if (window.htmx) {
            window.htmx.process(target);
        }
    }

    function switchTab(button) {
        var panel = document.getElementById(button.getAttribute("data-inspector-tab"));
        if (!panel) {
            return;
        }
        document.querySelectorAll(".inspector-tab-panel").forEach(function (p) {
            p.classList.remove("inspector-tab-panel--active");
        });
        document.querySelectorAll(".inspector-tab").forEach(function (t) {
            t.classList.remove("inspector-tab--active");
        });
        panel.classList.add("inspector-tab-panel--active");
        button.classList.add("inspector-tab--active");
    }

    document.addEventListener("click", function (event) {
        var target = event.target;
        if (!(target instanceof Element)) {
            return;
        }
        var toggle = target.closest("[data-inspector-toggle-polling]");
        if (toggle) {
            togglePolling(toggle);
            return;
        }
        var tab = target.closest("[data-inspector-tab]");
        if (tab) {
            switchTab(tab);
        }
    });
}());
