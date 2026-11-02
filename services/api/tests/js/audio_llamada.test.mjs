// Prueba del remuestreador del audio del navegador (static/audio-llamada.js). Corre con `node --test`.
import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const A = createRequire(import.meta.url)("../../app/static/audio-llamada.js");

const seno = (hz, tasa, seg, amp = 0.5) => Float32Array.from({ length: Math.round(tasa * seg) }, (_, i) => amp * Math.sin((2 * Math.PI * hz * i) / tasa));
const rms = (x, saltar = 0) => { let s = 0; for (let i = saltar; i < x.length; i++) s += x[i] * x[i]; return Math.sqrt(s / (x.length - saltar)); };
const db = (v) => 20 * Math.log10(v + 1e-12);
// Energía a una frecuencia (Goertzel), para medir réplicas.
const nivelEn = (x, hz, tasa, saltar = 0) => {
  const w = (2 * Math.PI * hz) / tasa; let re = 0, im = 0;
  for (let i = saltar; i < x.length; i++) { re += x[i] * Math.cos(w * i); im -= x[i] * Math.sin(w * i); }
  return (2 * Math.hypot(re, im)) / (x.length - saltar);
};
/** Lo que hacía el navegador antes: promediar 6 muestras. */
const promediar6 = (x) => Float32Array.from({ length: Math.floor(x.length / 6) }, (_, i) => { let s = 0; for (let k = 0; k < 6; k++) s += x[i * 6 + k]; return s / 6; });

function bajar(hz, tasa = 48000) {
  const r = A.crearRemuestreador(tasa, 8000);
  const y = r.procesar(seno(hz, tasa, 1));
  return db(rms(y, 200) / (0.5 / Math.SQRT2));  // dB respecto de la señal que entró
}

test("la banda de la voz pasa casi intacta", () => {
  for (const hz of [300, 1000, 2000, 3000]) assert.ok(bajar(hz) > -1, `${hz} Hz: ${bajar(hz).toFixed(1)} dB`);
});

test("sobre 4 kHz no entra casi nada (antes se plegaba sobre la voz)", () => {
  const nuevo = {}, viejo = {};
  for (const hz of [4400, 5000, 6000, 7000, 10000]) {
    nuevo[hz] = bajar(hz);
    viejo[hz] = db(rms(promediar6(seno(hz, 48000, 1)), 200) / (0.5 / Math.SQRT2));
  }
  console.log("alias en dB (nuevo / antes):", Object.keys(nuevo).map((h) => `${h}Hz ${nuevo[h].toFixed(0)}/${viejo[h].toFixed(0)}`).join("  "));
  for (const hz of [5000, 6000, 7000, 10000]) assert.ok(nuevo[hz] < -50, `${hz} Hz: ${nuevo[hz].toFixed(1)} dB`);
  assert.ok(nuevo[4400] < -25, `4400 Hz: ${nuevo[4400].toFixed(1)} dB`);
  assert.ok(viejo[5000] > -12 && viejo[6000] > -14, "el promedio de 6 dejaba pasar la «s» (referencia)");
});

test("tasa 44,1 kHz (no entera) también filtra y da la cantidad justa de muestras", () => {
  const r = A.crearRemuestreador(44100, 8000);
  const x = seno(1000, 44100, 1), y = r.procesar(x);
  assert.ok(Math.abs(y.length - 8000) <= 130, `salieron ${y.length}`);
  assert.ok(db(rms(y, 200) / (0.5 / Math.SQRT2)) > -1);
  assert.ok(bajar(6000, 44100) < -50);
});

test("ganancia 1 en continua", () => {
  const r = A.crearRemuestreador(48000, 8000);
  const y = r.procesar(new Float32Array(48000).fill(0.5));
  assert.ok(Math.abs(y[4000] - 0.5) < 1e-3, `salió ${y[4000]}`);
});

test("partir el audio en tramas da lo mismo que procesarlo entero", () => {
  const x = Float32Array.from({ length: 48000 }, (_, i) => 0.3 * Math.sin(i / 7) + 0.2 * Math.sin(i / 3.1));
  const entero = A.crearRemuestreador(48000, 8000).procesar(x);
  for (const tam of [128, 480, 4096]) {
    const r = A.crearRemuestreador(48000, 8000), partes = [];
    for (let i = 0; i < x.length; i += tam) partes.push(r.procesar(x.subarray(i, i + tam)));
    const junto = new Float32Array(partes.reduce((s, p) => s + p.length, 0)); let o = 0;
    for (const p of partes) { junto.set(p, o); o += p.length; }
    assert.equal(junto.length, entero.length, `tramas de ${tam}`);
    let maxDif = 0; for (let i = 0; i < junto.length; i++) maxDif = Math.max(maxDif, Math.abs(junto[i] - entero[i]));
    assert.ok(maxDif < 1e-5, `tramas de ${tam}: diferencia ${maxDif}`);
  }
});

test("subir de 8 a 48 kHz no deja réplicas de la voz sobre 4 kHz (repetir la última muestra sí)", () => {
  const x8 = seno(1000, 8000, 1);
  const y = A.crearRemuestreador(8000, 48000).procesar(x8);
  // El filtro retiene unas muestras del futuro (1,5 ms de demora): al final faltan ~70.
  assert.ok(y.length <= 48000 && y.length >= 47850, `salieron ${y.length}`);
  const tono = nivelEn(y, 1000, 48000, 2000);
  assert.ok(db(tono / 0.5) > -1, `el tono bajó a ${db(tono / 0.5).toFixed(1)} dB`);
  const replica = Math.max(nivelEn(y, 7000, 48000, 2000), nivelEn(y, 9000, 48000, 2000));
  const sostenido = Float32Array.from({ length: 48000 }, (_, i) => x8[Math.floor(i / 6)]);
  const replicaVieja = Math.max(nivelEn(sostenido, 7000, 48000, 2000), nivelEn(sostenido, 9000, 48000, 2000));
  console.log(`réplica a 7/9 kHz: ${db(replica / tono).toFixed(0)} dB (repitiendo muestras: ${db(replicaVieja / tono).toFixed(0)} dB)`);
  assert.ok(db(replica / tono) < -40);
  assert.ok(db(replicaVieja / tono) > -20);
});

test("Int16 <-> Float32 sin pasarse de la escala", () => {
  const e = A.aEntero(Float32Array.from([-2, -1, 0, 1, 2, 0.5]));
  assert.deepEqual(Array.from(e), [-32768, -32768, 0, 32767, 32767, 16383]);
  assert.equal(A.aFlotante(Int16Array.from([-32768, 0, 16384]))[2], 0.5);
});
