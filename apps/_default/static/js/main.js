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
