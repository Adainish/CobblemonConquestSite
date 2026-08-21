/* Cobblemon Conquest – main.js */

// Mobile nav toggle
document.addEventListener("DOMContentLoaded", function () {
    const toggle = document.querySelector(".nav-toggle");
    const links  = document.querySelector(".nav-links");
    if (toggle && links) {
        toggle.addEventListener("click", () => links.classList.toggle("open"));
    }

    // Copy server IP to clipboard
    const ipEl = document.getElementById("server-ip");
    if (ipEl) {
        ipEl.addEventListener("click", function () {
            const ip = this.dataset.ip;
            if (navigator.clipboard) {
                navigator.clipboard.writeText(ip).then(() => showToast("Copied: " + ip));
            } else {
                // fallback
                const ta = document.createElement("textarea");
                ta.value = ip;
                document.body.appendChild(ta);
                ta.select();
                document.execCommand("copy");
                document.body.removeChild(ta);
                showToast("Copied: " + ip);
            }
        });
    }

    // Live player count – fetch once on load then every 5 minutes
    const PLAYER_COUNT_INTERVAL = 5 * 60 * 1000; // 5 minutes in ms
    if (document.querySelector(".player-count")) {
        fetchPlayerCount();
        setInterval(fetchPlayerCount, PLAYER_COUNT_INTERVAL);
    }
});

function showToast(msg) {
    let toast = document.getElementById("copy-toast");
    if (!toast) {
        toast = document.createElement("div");
        toast.id = "copy-toast";
        document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.classList.add("show");
    setTimeout(() => toast.classList.remove("show"), 2200);
}

function fetchPlayerCount() {
    fetch("/api/player-count")
        .then(function (res) { return res.json(); })
        .then(function (data) {
            const online    = data.online    || 0;
            const max       = data.max       || 0;
            const reachable = data.reachable !== false;

            document.querySelectorAll(".player-count").forEach(function (el) {
                el.textContent = reachable
                    ? online + " / " + max + " online"
                    : "Server offline";
            });

            document.querySelectorAll(".status-dot").forEach(function (dot) {
                dot.classList.toggle("status-dot--online",  reachable);
                dot.classList.toggle("status-dot--offline", !reachable);
            });
        })
        .catch(function () {
            document.querySelectorAll(".player-count").forEach(function (el) {
                el.textContent = "Status unavailable";
            });
            document.querySelectorAll(".status-dot").forEach(function (dot) {
                dot.classList.remove("status-dot--online");
                dot.classList.add("status-dot--offline");
            });
        });
}
