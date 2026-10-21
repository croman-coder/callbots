/* Prueba de un agente de recepción sin micrófono: chat de texto y simulación (Claude hace de cliente).
   Cada respuesta del servidor trae las líneas nuevas y cómo quedó el caso (área, plazo, estado). */
(function () {
  "use strict";

  var raiz = document.querySelector("[data-conversar]");
  if (!raiz) return;
  var base = "/campaigns/" + raiz.dataset.campaign + "/recepcion/";

  function $(sel, el) { return (el || document).querySelector(sel); }

  function pedir(ruta, datos) {
    var fd = new FormData();
    Object.keys(datos).forEach(function (k) { fd.set(k, datos[k]); });
    return fetch(base + ruta, { method: "POST", body: fd, credentials: "same-origin" })
      .then(function (r) { return r.json().catch(function () { return { error: "El servidor no respondió bien." }; }); });
  }

  function pintarLineas(lista, lineas) {
    lineas.forEach(function (l) {
      var li = document.createElement("li"), b = document.createElement("b"), p = document.createElement("p");
      li.className = l.quien === "agente" || l.quien === "sistema" ? l.quien : "cliente";
      b.textContent = { agente: "Agente", sistema: "Consulta al sistema (no lo escucha el cliente)" }[l.quien] || "Cliente";
      p.textContent = l.texto;
      li.appendChild(b); li.appendChild(p); lista.appendChild(li);
    });
    lista.scrollTop = lista.scrollHeight;
  }

  // Cómo quedó el caso, en una línea: «Derivado a Repuestos (Valentina Ortiz) · le responden en 24 h · Ver caso».
  function pintarEstado(el, j) {
    el.textContent = "";
    if (!j || !j.caso_id) return;
    var partes = [];
    if (j.area) partes.push("Derivado a " + j.area + (j.responsable ? " (" + j.responsable + ")" : ""));
    else partes.push(j.estado);
    if (j.plazo_horas) partes.push("le responden en " + j.plazo_horas + " h");
    if (j.motivo) partes.push(j.motivo.toLowerCase());
    if (j.urgente) partes.push("urgente");
    el.appendChild(document.createTextNode(partes.join(" · ") + " · "));
    var a = document.createElement("a");
    a.className = "link"; a.href = j.enlace; a.textContent = "Ver caso #" + j.caso_id;
    el.appendChild(a);
  }

  // ------------------------------------------------------------ chat
  var chat = $("[data-chat]", raiz);
  if (chat) {
    var lineasChat = $("[data-lineas]", chat), estadoChat = $("[data-estado]", chat), form = $("[data-escribir]", chat);
    var entrada = $("input", form), enviar = $("button", form), casoChat = "";

    var empezar = function () {
      casoChat = ""; lineasChat.textContent = ""; estadoChat.textContent = "";
      entrada.disabled = false; enviar.disabled = false;
      pedir("chat", { caso_id: "", texto: "" }).then(function (j) {
        if (j.error) { estadoChat.textContent = j.error; return; }
        casoChat = String(j.caso_id); pintarLineas(lineasChat, j.lineas);
      });
    };

    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var texto = entrada.value.trim();
      if (!texto || !casoChat) return;
      pintarLineas(lineasChat, [{ quien: "cliente", texto: texto }]);
      entrada.value = ""; entrada.disabled = true; enviar.disabled = true;
      estadoChat.textContent = "El agente está escribiendo…";
      pedir("chat", { caso_id: casoChat, texto: texto }).then(function (j) {
        if (j.error) { estadoChat.textContent = j.error; entrada.disabled = false; enviar.disabled = false; return; }
        pintarLineas(lineasChat, j.lineas); pintarEstado(estadoChat, j);
        if (j.fin) { estadoChat.insertBefore(document.createTextNode("Conversación terminada. "), estadoChat.firstChild); return; }
        entrada.disabled = false; enviar.disabled = false; entrada.focus();
      }).catch(function () {
        estadoChat.textContent = "No se pudo enviar. Probá de nuevo."; entrada.disabled = false; enviar.disabled = false;
      });
    });
    $("[data-reiniciar]", chat).addEventListener("click", empezar);
    empezar();
  }

  // ------------------------------------------------------------ simulación
  var sim = $("[data-simular]", raiz);
  if (sim) {
    var lineasSim = $("[data-lineas]", sim), estadoSim = $("[data-estado]", sim), arrancar = $("[data-arrancar]", sim);
    var detalle = $("[data-perfil-detalle]", sim), propio = $("[data-perfil-propio]", sim), corriendo = false;

    sim.querySelectorAll("input[name=perfil]").forEach(function (r) {
      r.addEventListener("change", function () {
        propio.hidden = r.value !== "otro";
        detalle.textContent = r.value === "otro" ? "" : (r.dataset.texto || "");
        if (r.value === "otro") $("textarea", propio).focus();
      });
    });

    var vuelta = function (caso, perfil, texto) {
      return pedir("simular", { caso_id: caso, perfil: perfil, perfil_texto: texto }).then(function (j) {
        if (j.error) { estadoSim.textContent = j.error; return null; }
        pintarLineas(lineasSim, j.lineas); pintarEstado(estadoSim, j);
        if (j.fin) return null;
        return vuelta(String(j.caso_id), perfil, texto);
      });
    };

    arrancar.addEventListener("click", function () {
      if (corriendo) return;
      var elegido = sim.querySelector("input[name=perfil]:checked"), perfil = elegido ? elegido.value : "repuesto";
      var texto = perfil === "otro" ? $("textarea", propio).value.trim() : "";
      if (perfil === "otro" && texto.length < 10) { estadoSim.textContent = "Contá en una o dos frases la situación del cliente."; return; }
      corriendo = true; arrancar.disabled = true; lineasSim.textContent = "";
      estadoSim.textContent = "Simulando la llamada…";
      vuelta("", perfil, texto).catch(function () { estadoSim.textContent = "Se cortó la simulación. Probá de nuevo."; })
        .then(function () { corriendo = false; arrancar.disabled = false; arrancar.textContent = "Simular otra"; });
    });
  }
})();
