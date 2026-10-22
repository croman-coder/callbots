/* Grabador WAV desde el navegador, sin dependencias ni build. Lo usan Voces (clonar en el panel) y la página
   pública donde un locutor graba su voz a distancia (/grabar/<enlace>).

   Un bloque de grabación tiene: botón [data-grabar], reloj [data-reloj], nivel [data-nivel], <audio> y aviso
   [data-aviso]. Sin cancelación de eco ni control de ganancia: el clon copia lo que oye, mejor la voz tal cual. */
(function () {
  "use strict";

  function $(s, r) { return (r || document).querySelector(s); }

  var CAPTURA = "class Captura extends AudioWorkletProcessor{process(e){var c=e[0][0];if(c)this.port.postMessage(c.slice(0));return true}}registerProcessor('captura',Captura)";

  function wav(trozos, rate) {
    var n = trozos.reduce(function (a, t) { return a + t.length; }, 0);
    var buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf), o = 0;
    function txt(s) { for (var i = 0; i < s.length; i++) v.setUint8(o++, s.charCodeAt(i)); }
    function u32(x) { v.setUint32(o, x, true); o += 4; }
    function u16(x) { v.setUint16(o, x, true); o += 2; }
    txt("RIFF"); u32(36 + n * 2); txt("WAVE"); txt("fmt "); u32(16); u16(1); u16(1); u32(rate); u32(rate * 2); u16(2); u16(16);
    txt("data"); u32(n * 2);
    trozos.forEach(function (t) {
      for (var i = 0; i < t.length; i++) { var s = Math.max(-1, Math.min(1, t[i])); v.setInt16(o, s < 0 ? s * 32768 : s * 32767, true); o += 2; }
    });
    return new Blob([buf], { type: "audio/wav" });
  }

  // Un bloque de grabación: botón Grabar/Detener, reloj, nivel, reproductor y el WAV resultante.
  // minSeg: lo más corto que se acepta (3 s por defecto; las frases del locutor son más cortas).
  function Grabador(raiz, maxSeg, alCambiar, minSeg) {
    minSeg = minSeg || 3;
    var boton = $("[data-grabar]", raiz), reloj = $("[data-reloj]", raiz), nivel = $("[data-nivel]", raiz),
        audio = $("audio", raiz), aviso = $("[data-aviso]", raiz);
    var ctx = null, mic = null, nodo = null, trozos = [], inicio = 0, timer = null, grabando = false;
    this.blob = null;
    var self = this;

    function decir(msg) { aviso.textContent = msg || ""; }
    function parar() {
      if (!grabando) return;
      grabando = false; clearInterval(timer);
      if (nodo) nodo.port.onmessage = null;
      if (mic) mic.getTracks().forEach(function (t) { t.stop(); });
      var rate = ctx ? ctx.sampleRate : 48000;
      if (ctx) ctx.close();
      var seg = trozos.reduce(function (a, t) { return a + t.length; }, 0) / rate;
      boton.textContent = "Grabar de nuevo"; boton.classList.remove("grabando"); nivel.style.setProperty("--nivel", "0");
      if (seg < minSeg) { self.blob = null; audio.hidden = true; decir("Muy corto: grabá de nuevo leyendo el texto completo."); alCambiar(); return; }
      self.blob = wav(trozos, rate);
      audio.src = URL.createObjectURL(self.blob); audio.hidden = false;
      decir("Escuchala antes de seguir: tiene que oírse clara y sin ruido de fondo.");
      alCambiar();
    }
    function empezar() {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        decir("Este navegador no deja usar el micrófono acá. Probá con Chrome o Edge actualizados."); return;
      }
      // Sin cancelación de eco ni control de ganancia: el clon copia lo que oye, mejor la voz tal cual.
      navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: false, noiseSuppression: true, autoGainControl: false } })
        .then(function (stream) {
          mic = stream; ctx = new (window.AudioContext || window.webkitAudioContext)();
          var url = URL.createObjectURL(new Blob([CAPTURA], { type: "application/javascript" }));
          return ctx.audioWorklet.addModule(url).then(function () {
            nodo = new AudioWorkletNode(ctx, "captura");
            nodo.port.onmessage = function (e) {
              if (!grabando) return;
              trozos.push(e.data);
              var pico = 0; for (var i = 0; i < e.data.length; i++) pico = Math.max(pico, Math.abs(e.data[i]));
              nivel.style.setProperty("--nivel", String(Math.min(1, pico * 1.4)));
            };
            ctx.createMediaStreamSource(stream).connect(nodo);
            trozos = []; grabando = true; inicio = Date.now(); self.blob = null; audio.hidden = true; alCambiar();
            boton.textContent = "Detener"; boton.classList.add("grabando"); decir("Grabando… leé el texto con calma, como en una llamada.");
            timer = setInterval(function () {
              var s = (Date.now() - inicio) / 1000;
              reloj.textContent = "0:" + String(Math.floor(s)).padStart(2, "0") + " / 0:" + String(maxSeg).padStart(2, "0");
              if (s >= maxSeg) parar();
            }, 200);
          });
        })
        .catch(function (err) {
          var negado = err && (err.name === "NotAllowedError" || err.name === "SecurityError");
          decir(negado ? "No diste permiso para usar el micrófono. Habilitalo en el candado de la barra de direcciones."
                       : "No se pudo usar el micrófono: " + (err && err.message ? err.message : "error desconocido"));
        });
    }
    boton.addEventListener("click", function () { if (grabando) parar(); else empezar(); });
    this.usarArchivo = function (archivo) {
      self.blob = archivo || null;
      if (archivo) { audio.src = URL.createObjectURL(archivo); audio.hidden = false; decir("Audio subido. Escuchalo y revisá que el texto de abajo sea lo que se dice."); }
      alCambiar();
    };
  }

  window.CallbotGrabador = { Grabador: Grabador, wav: wav };
})();
