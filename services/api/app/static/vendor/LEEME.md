# Librerías propias del panel

- `motion.min.js`: [Motion](https://motion.dev) 12.43.0 (MIT), solo `animate` (versión mini, basada en la Web
  Animations API), `stagger` y `spring`. Se arma con esbuild a partir del paquete `motion`:

  ```js
  // entrada.js
  export { animate } from "motion/mini";
  export { stagger, spring } from "motion";
  ```
  ```bash
  npm i motion@12.43.0 esbuild
  npx esbuild entrada.js --bundle --minify --format=iife --global-name=Motion --target=es2019 --outfile=motion.min.js
  ```

  Se aloja acá y no se carga de un CDN: el panel no depende de servidores externos. Expone `window.Motion`.
  La licencia está en `LICENSE-Motion.md`.
