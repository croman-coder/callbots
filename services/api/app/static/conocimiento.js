/* Conocimiento: confirmar antes de borrar y probar la base con una pregunta. */
(function () {
  "use strict";
  document.querySelectorAll("form[data-confirmar]").forEach(function (f) {
    f.addEventListener("submit", function (e) { if (!confirm(f.dataset.confirmar)) e.preventDefault(); });
  });
  var probar = document.querySelector("[data-probar-base]");
  if (!probar) return;
  var salida = probar.querySelector("[data-respuesta]"), boton = probar.querySelector("button[type=submit]");
  probar.addEventListener("submit", function (e) {
    e.preventDefault();
    var pregunta = probar.querySelector("[name=pregunta]").value.trim();
    if (!pregunta) { salida.textContent = "Escribí una pregunta."; return; }
    boton.disabled = true; salida.textContent = "Buscando en la base…";
    fetch("/conocimiento/" + probar.dataset.probarBase + "/probar", { method: "POST", body: new URLSearchParams({ pregunta: pregunta }), credentials: "same-origin" })
      .then(function (r) { return r.json().catch(function () { return {}; }); })
      .then(function (j) { salida.textContent = j.respuesta ? "«" + j.respuesta + "»" : (j.error || "No se pudo probar."); })
      .catch(function () { salida.textContent = "No se pudo probar. Probá de nuevo."; })
      .then(function () { boton.disabled = false; });
  });
})();
