#!/usr/bin/env bash
# Verificación de carga de un desktop plugin de Hermes ANTES de empaquetar.
#
# LECCIÓN: `node --check archivo.js` da FALSOS OK para ESM (parsea distinto).
# Un paréntesis roto en un .js pasó el check y el plugin NO cargó en el desktop.
# Este script chequea en modo módulo (.mjs) Y ADEMÁS importa el archivo con
# stubs de react / react-jsx-runtime / @hermes/plugin-sdk, simulando la carga.
#
# Uso:  bash plugin-load-test.sh [/ruta/al/plugin.js]
set -euo pipefail

PLUGIN="${1:-$(cd "$(dirname "$0")/.." && pwd)/desktop/plugin.js}"
TMP=/tmp/plugin-load-test
rm -rf "$TMP" && mkdir -p "$TMP/node_modules/@hermes/plugin-sdk" "$TMP/node_modules/react"

cp "$PLUGIN" "$TMP/check.mjs"
if node --check "$TMP/check.mjs"; then
  echo "✓ sintaxis ESM (modo módulo) OK"
else
  echo "✗ ERROR DE SINTAXIS ESM — NO empaquetar"; exit 1
fi

printf '{"type":"module"}\n' > "$TMP/package.json"
cat > "$TMP/node_modules/react/package.json" <<'EOF'
{"name":"react","type":"module","exports":{".":"./index.js","./jsx-runtime":"./jsx-runtime.js"}}
EOF
cat > "$TMP/node_modules/react/index.js" <<'EOF'
export const useState = (v) => [typeof v === 'function' ? v() : v, () => {}]
export const useEffect = () => {}
export const useRef = (v) => ({ current: v === undefined ? null : v })
EOF
cat > "$TMP/node_modules/react/jsx-runtime.js" <<'EOF'
export const jsx = () => null
export const jsxs = () => null
EOF
cat > "$TMP/node_modules/@hermes/plugin-sdk/package.json" <<'EOF'
{"name":"@hermes/plugin-sdk","type":"module","main":"index.js"}
EOF
cat > "$TMP/node_modules/@hermes/plugin-sdk/index.js" <<'EOF'
export const host = { state: {}, request: async () => {}, onEvent: () => () => {} }
export const useValue = (x) => x
export const Popover = () => null
export const PopoverContent = () => null
export const PopoverTrigger = () => null
export const Select = () => null
export const SelectContent = () => null
export const SelectItem = () => null
export const SelectTrigger = () => null
export const SelectValue = () => null
export const Switch = () => null
export const Button = () => null
EOF

cp "$PLUGIN" "$TMP/plugin-test.js"
node -e "
import('$TMP/plugin-test.js').then(m => {
  const id = m.default && m.default.id
  const reg = typeof (m.default && m.default.register)
  if (!id || reg !== 'function') { console.error('✗ el módulo no exporta {id, register()}'); process.exit(1) }
  console.log('✓ módulo importa OK — id:', id, '| register:', reg)
}).catch(e => { console.error('✗ LOAD ERROR:', String(e && e.stack || e).slice(0, 900)); process.exit(1) })
"
echo "✓ LISTO para empaquetar"
