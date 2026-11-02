/* Audio de las pruebas desde el navegador (la prueba del agente y el simulador): el micrófono baja a 8 kHz hacia el
   agente y la voz del agente, que llega a 8 kHz, sube a la tasa de la placa de sonido.

   Antes se bajaba promediando 6 muestras y se subía repitiendo la última (o con el remuestreo de cada trama suelta del
   navegador). Promediar no es un filtro: todo lo que está sobre 4 kHz (la «s», la «z» y la «f» de «diez», «seis»,
   «cinco») se pliega sobre la voz como un soplido y Whisper entendía «Dies», «Vies» o nada (medido el 2/11/2026: más
   de la mitad de la energía de un «diez» quedaba sobre 3 kHz, que en un teléfono de verdad casi no existe). Repetir
   muestras agrega una réplica de la voz sobre 4 kHz y cada trama suelta metía un clic cada 20 ms.

   Acá el remuestreo es un filtro pasa-bajos de verdad (sinc con ventana de Blackman, corte a 3,6 kHz como la
   telefonía) que guarda su estado entre tramas, y la voz del agente sale por un solo nodo continuo con un colchón. */
(function (raiz) {
  "use strict";

  var LOBULOS = 10;     // cruces por cero a cada lado del núcleo: más es un corte más seco y más cuenta
  var FASES = 128;      // posiciones fraccionarias precalculadas del núcleo (se interpola entre dos)
  var CORTE_HZ = 3600;  // la banda de la telefonía (el teléfono corta cerca de 3,4 kHz)

  function sinc(x) { return x === 0 ? 1 : Math.sin(Math.PI * x) / (Math.PI * x); }
  function blackman(x) { return x <= -1 || x >= 1 ? 0 : 0.42 + 0.5 * Math.cos(Math.PI * x) + 0.08 * Math.cos(2 * Math.PI * x); }

  /** Remuestreador por bloques: `procesar(Float32Array)` devuelve las muestras nuevas a `tasaSalida`. Recuerda el final de
   *  cada bloque, así que partir el audio en tramas (de 128 o de 4096 muestras) da lo mismo que procesarlo entero. */
  function crearRemuestreador(tasaEntrada, tasaSalida, corteHz) {
    var corte = Math.min(corteHz || CORTE_HZ, 0.45 * tasaEntrada, 0.45 * tasaSalida);
    var fcn = corte / tasaEntrada;                   // ciclos por muestra de entrada
    var L = Math.ceil(LOBULOS / (2 * fcn));          // mitad del núcleo, en muestras de entrada
    var N = 2 * L;
    var razon = tasaEntrada / tasaSalida;
    var nucleo = new Float32Array((FASES + 1) * N);
    for (var q = 0; q <= FASES; q++) {
      var suma = 0, fila = q * N;
      for (var m = 0; m < N; m++) {
        var u = (m - L + 1) - q / FASES;
        var h = 2 * fcn * sinc(2 * fcn * u) * blackman(u / L);
        nucleo[fila + m] = h; suma += h;
      }
      for (var k = 0; k < N; k++) nucleo[fila + k] /= suma;   // ganancia 1 a frecuencia cero, sin importar la fase
    }
    var buf, p;
    function reiniciar() { buf = new Float32Array(L); p = L; }
    reiniciar();

    function procesar(entrada) {
      var nuevo = new Float32Array(buf.length + entrada.length);
      nuevo.set(buf); nuevo.set(entrada, buf.length);
      buf = nuevo;
      var salida = new Float32Array(Math.ceil(entrada.length / razon) + 4), n = 0;
      for (;;) {
        var i0 = Math.floor(p);
        if (i0 + L >= buf.length) break;               // faltan muestras del futuro: se sigue con el próximo bloque
        var qf = (p - i0) * FASES, qi = Math.floor(qf), t = qf - qi;
        var a = qi * N, b = a + N, desde = i0 - L + 1, y = 0;
        for (var j = 0; j < N; j++) y += buf[desde + j] * (nucleo[a + j] * (1 - t) + nucleo[b + j] * t);
        salida[n++] = y;
        p += razon;
      }
      var tirar = Math.floor(p) - L + 1;
      if (tirar > 0) { buf = buf.slice(tirar); p -= tirar; }
      return salida.subarray(0, n);
    }
    return { procesar: procesar, reiniciar: reiniciar };
  }

  /** Int16 -> Float32 en [-1, 1). */
  function aFlotante(int16) {
    var f = new Float32Array(int16.length);
    for (var i = 0; i < int16.length; i++) f[i] = int16[i] / 32768;
    return f;
  }

  /** Float32 -> Int16 con tope (nunca se pasa de la escala). */
  function aEntero(f32) {
    var s = new Int16Array(f32.length);
    for (var i = 0; i < f32.length; i++) {
      var v = Math.max(-1, Math.min(1, f32[i]));
      s[i] = v < 0 ? v * 32768 : v * 32767;
    }
    return s;
  }

  // El nodo que reproduce: una cola continua. Espera un colchón antes de arrancar y, si se queda sin audio (la red
  // se atrasó), vuelve a juntar un poco en vez de entrecortar. Avisa cuánto le queda para saber cuándo termina de hablar.
  var CODIGO_REPRODUCTOR =
    "class Reproductor extends AudioWorkletProcessor{" +
    "constructor(o){super();var c=o.processorOptions||{};this.cola=[];this.pos=0;this.largo=0;this.sonando=false;this.vueltas=0;" +
    "this.arranque=Math.round(sampleRate*(c.arranqueMs||100)/1000);this.reanudar=Math.round(sampleRate*(c.reanudarMs||60)/1000);" +
    "this.port.onmessage=function(e){this.cola.push(e.data);this.largo+=e.data.length}.bind(this)}" +
    "process(_,s){var out=s[0][0],n=out.length,i=0;" +
    "if(!this.sonando){if(this.largo<(this.reiniciado?this.reanudar:this.arranque)){out.fill(0);this.avisar();return true}this.sonando=true}" +
    "while(i<n&&this.cola.length){var b=this.cola[0],t=Math.min(n-i,b.length-this.pos);out.set(b.subarray(this.pos,this.pos+t),i);i+=t;this.pos+=t;this.largo-=t;if(this.pos>=b.length){this.cola.shift();this.pos=0}}" +
    "if(i<n){out.fill(0,i);this.sonando=false;this.reiniciado=true}" +
    "this.avisar();return true}" +
    "avisar(){if(++this.vueltas%8===0)this.port.postMessage({pendiente:this.largo/sampleRate})}}" +
    "registerProcessor('reproductor',Reproductor)";

  /** Crea el reproductor sobre `ctx`. Devuelve una promesa de { encolar(Int16Array a 8 kHz), pendienteS(), nodo }. */
  function crearReproductor(ctx, opciones) {
    var url = URL.createObjectURL(new Blob([CODIGO_REPRODUCTOR], { type: "application/javascript" }));
    return ctx.audioWorklet.addModule(url).then(function () {
      URL.revokeObjectURL(url);
      var nodo = new AudioWorkletNode(ctx, "reproductor", {
        numberOfInputs: 0, outputChannelCount: [1], processorOptions: opciones || {}
      });
      var subir = crearRemuestreador(8000, ctx.sampleRate);
      var pendiente = 0;
      nodo.port.onmessage = function (e) { if (e.data && typeof e.data.pendiente === "number") pendiente = e.data.pendiente; };
      nodo.connect(ctx.destination);
      return {
        nodo: nodo,
        encolar: function (int16) {
          var y = subir.procesar(aFlotante(int16));
          if (y.length) nodo.port.postMessage(y, [y.buffer]);
        },
        pendienteS: function () { return pendiente; }
      };
    });
  }

  var api = {
    crearRemuestreador: crearRemuestreador, crearReproductor: crearReproductor,
    aFlotante: aFlotante, aEntero: aEntero, CODIGO_REPRODUCTOR: CODIGO_REPRODUCTOR
  };
  if (typeof module === "object" && module.exports) module.exports = api;
  else raiz.AudioLlamada = api;
})(typeof window !== "undefined" ? window : globalThis);
