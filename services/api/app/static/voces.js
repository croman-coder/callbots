/* Voces: escuchar la biblioteca y clonar la voz de una persona con su permiso grabado.
   Graba en WAV desde el navegador (sin dependencias ni build). */
(function () {
  "use strict";

  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }

  // ------------------------------------------------------------ Escuchar
  var sonando = null, botonSonando = null;
  function escuchar(id, boton, estado) {
    if (sonando) { sonando.pause(); sonando = null; }
    if (botonSonando) botonSonando.classList.remove("sonando");
    if (botonSonando === boton) { botonSonando = null; return; }  // segundo toque: para
    botonSonando = boton; boton.classList.add("sonando");
    if (estado) estado.textContent = "Generando la muestra…";
    sonando = new Audio("/voices/preview?profile_id=" + encodeURIComponent(id));
    function fin(msg) { boton.classList.remove("sonando"); botonSonando = null; if (estado) estado.textContent = msg || ""; }
    sonando.addEventListener("playing", function () { if (estado) estado.textContent = "Escuchando…"; });
    sonando.addEventListener("ended", function () { fin(""); });
    sonando.addEventListener("error", function () { fin("No se pudo generar la muestra. Probá de nuevo."); });
    sonando.play().catch(function () {});
  }
  $$("[data-escuchar]").forEach(function (b) {
    b.addEventListener("click", function () { escuchar(b.dataset.escuchar, b, $("[data-estado-biblioteca]")); });
  });

  // ------------------------------------------------------------ Crear una voz con IA
  var disenar = $("#form-disenar");
  if (disenar) {
    var btnProbar = $("[data-probar]", disenar), btnGuardar = $("[data-guardar]", disenar), estadoDi = $("[data-estado-diseno]", disenar),
        cajaMuestra = $("[data-muestra]", disenar), audioDi = $("[data-audio-diseno]", disenar), codigoDi = "";
    function decirDi(t) { estadoDi.textContent = t || ""; }
    function respuesta(r) { return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) throw new Error(j.error || "No se pudo."); return j; }); }
    // Cambiar un rasgo deja la muestra vieja sin sentido: se oculta, para no guardar una voz que no es la que dice el formulario.
    $$("select", disenar).forEach(function (s) {
      s.addEventListener("change", function () { codigoDi = ""; cajaMuestra.hidden = true; audioDi.pause(); decirDi("Tocá «Probar una voz» con los rasgos nuevos."); });
    });
    btnProbar.addEventListener("click", function () {
      btnProbar.disabled = true; btnProbar.textContent = "Generando…"; decirDi("Inventando la voz, unos segundos…");
      fetch("/voices/disenar/probar", { method: "POST", body: new URLSearchParams(new FormData(disenar)), credentials: "same-origin" })
        .then(respuesta)
        .then(function (j) {
          codigoDi = j.codigo; audioDi.src = j.audio; cajaMuestra.hidden = false;
          audioDi.play().catch(function () {});
          decirDi("Si no te convence, tocá «Probar otra variante»: cada una suena distinta.");
          btnProbar.textContent = "Probar otra variante";
        })
        .catch(function (e) { decirDi(e.message); btnProbar.textContent = codigoDi ? "Probar otra variante" : "Probar una voz"; })
        .then(function () { btnProbar.disabled = false; });
    });
    disenar.addEventListener("submit", function (e) {
      e.preventDefault();
      var nombre = $("#di-nombre").value.trim();
      if (!codigoDi) { decirDi("Primero probá una voz."); return; }
      if (!nombre) { decirDi("Ponele un nombre a la voz."); $("#di-nombre").focus(); return; }
      btnGuardar.disabled = true; btnGuardar.textContent = "Guardando…"; decirDi("Guardando la voz y probándola…");
      var datos = new URLSearchParams({ codigo: codigoDi, nombre: nombre, genero: $("#di-genero").value });
      fetch("/voices/disenar/guardar", { method: "POST", body: datos, credentials: "same-origin" })
        .then(respuesta)
        .then(function () { location.href = "/voices?ok=" + encodeURIComponent("Voz «" + nombre + "» guardada. Elegila en la ficha de un agente, pestaña Configuración."); })
        .catch(function (e) { decirDi(e.message); btnGuardar.disabled = false; btnGuardar.textContent = "Quedarme con esta voz"; });
    });
  }

  // ------------------------------------------------------------ Pronunciación: oír cómo queda una palabra
  var vozPrueba = $("[data-voz-prueba]"), estadoPron = $("[data-estado-pronunciacion]");
  $$("[data-probar-dicho]").forEach(function (b) {
    b.addEventListener("click", function () {
      if (!vozPrueba) return;
      if (sonando) { sonando.pause(); sonando = null; }
      if (botonSonando) botonSonando.classList.remove("sonando");
      botonSonando = b; b.classList.add("sonando"); if (estadoPron) estadoPron.textContent = "Generando…";
      // Con `literal` el servidor dice el texto tal cual (ya es «cómo se dice»): no le aplica el diccionario otra vez.
      sonando = new Audio("/voices/preview?literal=1&profile_id=" + encodeURIComponent(vozPrueba.value) + "&text=" + encodeURIComponent(b.dataset.probarDicho));
      function fin(msg) { b.classList.remove("sonando"); botonSonando = null; if (estadoPron) estadoPron.textContent = msg || ""; }
      sonando.addEventListener("playing", function () { if (estadoPron) estadoPron.textContent = "Escuchando…"; });
      sonando.addEventListener("ended", function () { fin("Tocá ▶ para oír cómo queda."); });
      sonando.addEventListener("error", function () { fin("No se pudo generar la muestra. Probá de nuevo."); });
      sonando.play().catch(function () {});
    });
  });

  // Locutor a distancia: copiar el enlace para mandárselo.
  $$("[data-copiar]").forEach(function (b) {
    b.addEventListener("click", function () {
      var listo = function () { b.textContent = "Copiado"; setTimeout(function () { b.textContent = "Copiar enlace"; }, 1800); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(b.dataset.copiar).then(listo, function () {});
      else { var r = document.createRange(); r.selectNodeContents(b.previousElementSibling); var s = getSelection(); s.removeAllRanges(); s.addRange(r); }
    });
  });

  $$("form[data-confirmar]").forEach(function (f) {
    f.addEventListener("submit", function (e) { if (!confirm(f.dataset.confirmar)) e.preventDefault(); });
  });

  // El grabador (WAV desde el navegador) vive en grabador.js: lo comparte la página pública del locutor.
  var Grabador = window.CallbotGrabador && window.CallbotGrabador.Grabador;

  // ------------------------------------------------------------ Clonar
  var form = document.getElementById("form-clonar");
  if (!form) return;
  var estado = $("[data-estado-clon]", form), enviar = $("[data-clonar]", form), persona = form.elements.persona,
      frase = $("[data-frase-permiso]", form), texto = form.elements.texto, archivo = $("[data-subir]", form);

  function revisar() {
    var listo = form.elements.nombre.value.trim() && persona.value.trim() && form.elements.genero.value &&
                form.elements.acepto.checked && permiso.blob && voz.blob && texto.value.trim().length >= 20;
    enviar.disabled = !listo;
    if (!enviando) estado.textContent = listo ? "Todo listo: tocá «Clonar voz»." : "Completá los tres pasos para clonar.";
  }
  var enviando = false;
  var permiso = new Grabador($("[data-bloque=permiso]", form), 15, revisar);
  var maxVoz = parseFloat($("[data-bloque=voz]", form).dataset.max) || 7;
  var voz = new Grabador($("[data-bloque=voz]", form), maxVoz, revisar);

  persona.addEventListener("input", function () {
    frase.textContent = frase.dataset.plantilla.replace("{persona}", persona.value.trim() || "(tu nombre y apellido)");
  });
  archivo.addEventListener("change", function () {
    var f = archivo.files && archivo.files[0];
    if (f && f.size > 15 * 1024 * 1024) { archivo.value = ""; estado.textContent = "El audio es demasiado grande: alcanza con unos 5 segundos."; return; }
    if (!f) { voz.usarArchivo(null); return; }
    // El largo se mira antes de aceptarlo: la placa de video del servidor solo alcanza para referencias cortas.
    var prueba = new Audio(URL.createObjectURL(f));
    prueba.addEventListener("loadedmetadata", function () {
      if (isFinite(prueba.duration) && prueba.duration > maxVoz + 0.5) {
        archivo.value = ""; voz.usarArchivo(null);
        estado.textContent = "Ese audio dura " + Math.round(prueba.duration) + " segundos: para clonar alcanza con unos 5 (hasta " + maxVoz + ").";
        return;
      }
      voz.usarArchivo(f);
    });
    prueba.addEventListener("error", function () { voz.usarArchivo(f); });
  });
  form.addEventListener("input", revisar);
  form.addEventListener("change", revisar);

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    if (enviar.disabled) return;
    var datos = new FormData();
    ["nombre", "genero", "persona", "texto"].forEach(function (k) { datos.append(k, form.elements[k].value); });
    datos.append("acepto", form.elements.acepto.checked ? "si" : "");
    datos.append("consentimiento", permiso.blob, "consentimiento.wav");
    datos.append("referencia", voz.blob, voz.blob.name || "referencia.wav");
    enviar.disabled = true; enviando = true; estado.textContent = "Clonando la voz… puede tardar unos segundos.";
    fetch("/voices/clonar", { method: "POST", body: datos, headers: { Accept: "application/json" } })
      .then(function (r) { return r.json().catch(function () { return { error: "Respuesta inesperada del servidor." }; }); })
      .then(function (j) {
        if (j.error) { estado.textContent = j.error; enviar.disabled = false; enviando = false; return; }
        location.href = "/voices?ok=" + encodeURIComponent("Voz clonada: " + j.nombre + ". Ya la podés elegir en un agente.") + "#biblioteca";
      })
      .catch(function () { estado.textContent = "No se pudo conectar con el servidor. Probá de nuevo."; enviar.disabled = false; enviando = false; });
  });
  revisar();
})();
