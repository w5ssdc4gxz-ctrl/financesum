'use client'

import { useRef, useState, useMemo } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { useTexture, Float, Points, PointMaterial, Environment, PerspectiveCamera } from '@react-three/drei'
import * as THREE from 'three'
import { Vector3 } from 'three'
import * as random from 'maath/random'
import { useTheme } from 'next-themes'

// --- Particle Globe (The "Living Data") ---
function ParticleGlobe(props: any) {
  const ref = useRef<any>()
  
  // Generate random points on a sphere
  const [sphere] = useState(() => {
    // Generate 4000 points on a sphere of radius 1.2
    // We use a slightly smaller radius so it sits "inside" or "behind" the floating screens
    const data = random.inSphere(new Float32Array(4000) as any, { radius: 2.5 })
    return data
  })

  useFrame((state, delta) => {
    if (ref.current) {
      ref.current.rotation.x -= delta / 15
      ref.current.rotation.y -= delta / 20
    }
  })

  return (
    <group rotation={[0, 0, Math.PI / 4]} {...props}>
      <Points ref={ref} positions={sphere as Float32Array} stride={3} frustumCulled={false}>
        <PointMaterial
          transparent
          color="#0015ff" // Brand Blue
          size={0.006}
          sizeAttenuation={true}
          depthWrite={false}
          opacity={0.6}
        />
      </Points>
    </group>
  )
}

// --- Image Plane (The "Product") ---
function ImagePlane({ url, position, rotation, scale = 1, transparent = false, opacity = 1 }: any) {
  const texture = useTexture(url) as THREE.Texture
  // Fix color space to ensure images don't look washed out
  texture.colorSpace = THREE.SRGBColorSpace 
  
  const mesh = useRef<THREE.Mesh>(null!)
  const [hovered, setHover] = useState(false)
  
  useFrame((state, delta) => {
    if (mesh.current) {
        const targetScale = hovered ? scale * 1.05 : scale
        mesh.current.scale.lerp(new Vector3(targetScale, targetScale, targetScale), 0.1)
    }
  })

  return (
    <Float speed={2} rotationIntensity={0.1} floatIntensity={0.2} floatingRange={[-0.05, 0.05]}>
      <mesh 
        ref={mesh} 
        position={position} 
        rotation={rotation}
        onPointerOver={() => setHover(true)}
        onPointerOut={() => setHover(false)}
      >
        <planeGeometry args={[3, 2]} />
        <meshBasicMaterial 
            map={texture} 
            transparent={true} 
            opacity={opacity}
            // Using BasicMaterial ensures the image colors are EXACT 
            // and not affected by lighting (no tinting/shadows)
            toneMapped={false}
        />
      </mesh>
    </Float>
  )
}

function HeroScene() {
  const { mouse, viewport } = useThree()
  const group = useRef<THREE.Group>(null!)

  useFrame((state, delta) => {
    const x = (mouse.x * viewport.width) / 80 // Reduced sensitivity
    const y = (mouse.y * viewport.height) / 80
    group.current.rotation.x = THREE.MathUtils.lerp(group.current.rotation.x, -y, 0.05)
    group.current.rotation.y = THREE.MathUtils.lerp(group.current.rotation.y, x, 0.05)
  })

  return (
    <group ref={group}>
      {/* 1. The Data Cloud Background */}
      <ParticleGlobe />

      {/* 2. The Product Screens */}
      {/* Central Hero Image */}
      <ImagePlane 
        url="/hero/hero-1.png" 
        position={[0, 0, 0.5]} 
        rotation={[0, 0, 0]} 
        scale={1.8} 
        opacity={1}
      />

      {/* Satellite Images */}
      <ImagePlane 
        url="/hero/hero-2.png" 
        position={[3.2, 1.2, -0.5]} 
        rotation={[0, -0.15, 0.05]} 
        scale={1.2}
      />
      <ImagePlane 
        url="/hero/hero-3.png" 
        position={[-3.2, 1.0, -1.0]} 
        rotation={[0, 0.15, -0.05]} 
        scale={1.2}
      />
      <ImagePlane 
        url="/hero/hero-4.png" 
        position={[2.8, -1.8, -0.2]} 
        rotation={[0.05, -0.1, -0.02]} 
        scale={1.3}
      />
      <ImagePlane 
        url="/hero/hero-5.png" 
        position={[-2.9, -1.5, -0.2]} 
        rotation={[0.05, 0.1, 0.02]} 
        scale={1.1}
      />
    </group>
  )
}

export default function Hero3D() {
  return (
    <div className="absolute inset-0 z-0 w-full h-full">
      <Canvas dpr={[1, 2]} gl={{ antialias: true, alpha: true }}>
        <PerspectiveCamera makeDefault position={[0, 0, 8]} fov={50} />
        <HeroScene />
        {/* We use MeshBasicMaterial for images, so we don't need strong lights for them. 
            But we can keep ambient light just in case we add other standard meshes. */}
        <ambientLight intensity={1} />
      </Canvas>
    </div>
  )
}
