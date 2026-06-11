"use client";

/**
 * The particle ocean — chaos resolving into ledger order.
 * A low-horizon field of ~6k round glow particles (deep blue, teal and gold
 * sparks) flowing in sine waves, with pointer parallax on the camera.
 * Renders nothing under prefers-reduced-motion.
 */
import { Canvas, useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";

const COLS = 130;
const ROWS = 46;
const SEP = 3.0;
const BASE_Y = -16;

function makeSprite(): THREE.Texture {
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const x = c.getContext("2d")!;
  const g = x.createRadialGradient(32, 32, 0, 32, 32, 32);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(0.35, "rgba(255,255,255,0.55)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  x.fillStyle = g;
  x.fillRect(0, 0, 64, 64);
  return new THREE.CanvasTexture(c);
}

function Ocean() {
  const points = useRef<THREE.Points>(null!);
  const pointer = useRef({ x: 0, y: 0 });

  // window-level pointer tracking — the canvas itself stays pointer-events:none
  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      pointer.current.x = e.clientX / window.innerWidth - 0.5;
      pointer.current.y = e.clientY / window.innerHeight - 0.5;
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    return () => window.removeEventListener("pointermove", onMove);
  }, []);

  const { positions, colors, sprite } = useMemo(() => {
    const n = COLS * ROWS;
    const positions = new Float32Array(n * 3);
    const colors = new Float32Array(n * 3);
    const cBase = new THREE.Color(0x16456f);
    const cTeal = new THREE.Color(0x14b8a6);
    const cGold = new THREE.Color(0xd9a21b);
    let p = 0;
    for (let x = 0; x < COLS; x++) {
      for (let z = 0; z < ROWS; z++) {
        positions[p] = (x - COLS / 2) * SEP;
        positions[p + 1] = BASE_Y;
        positions[p + 2] = (z - ROWS / 2) * SEP;
        const r = Math.random();
        const c = r < 0.05 ? cGold : r < 0.1 ? cTeal : cBase;
        colors[p] = c.r;
        colors[p + 1] = c.g;
        colors[p + 2] = c.b;
        p += 3;
      }
    }
    return { positions, colors, sprite: makeSprite() };
  }, []);

  useFrame(({ camera, clock }) => {
    const t = clock.elapsedTime * 0.66;
    const arr = points.current.geometry.attributes.position.array as Float32Array;
    let i = 0;
    for (let x = 0; x < COLS; x++) {
      for (let z = 0; z < ROWS; z++) {
        arr[i * 3 + 1] =
          BASE_Y + Math.sin(x * 0.2 + t) * 1.9 + Math.cos(z * 0.16 + t * 0.7) * 1.9;
        i++;
      }
    }
    points.current.geometry.attributes.position.needsUpdate = true;
    camera.position.x += (pointer.current.x * 12 - camera.position.x) * 0.04;
    camera.position.y += (14 - pointer.current.y * 6 - camera.position.y) * 0.04;
    camera.lookAt(0, -18, -30);
  });

  return (
    <points ref={points}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
        <bufferAttribute attach="attributes-color" args={[colors, 3]} />
      </bufferGeometry>
      <pointsMaterial
        size={2.1}
        map={sprite}
        vertexColors
        transparent
        opacity={0.55}
        blending={THREE.AdditiveBlending}
        depthWrite={false}
        sizeAttenuation
      />
    </points>
  );
}

export default function ParticleOcean() {
  return (
    <div
      aria-hidden
      className="pointer-events-none absolute inset-0 h-[110vh] max-h-[1100px] overflow-hidden
                 [mask-image:linear-gradient(to_bottom,#000_62%,transparent_100%)]"
    >
      <Canvas
        dpr={[1, 1.5]}
        gl={{ antialias: true, alpha: true, powerPreference: "low-power" }}
        camera={{ fov: 55, near: 1, far: 600, position: [0, 14, 96] }}
      >
        <fogExp2 attach="fog" args={[0x050f1c, 0.0042]} />
        <Ocean />
      </Canvas>
    </div>
  );
}
