import { useEffect, useRef } from 'react';

interface AetherFlowHeroProps {
  dark?: boolean;
}

interface ParticleState {
  x: number;
  y: number;
  directionX: number;
  directionY: number;
  size: number;
}

/** Interactive particle field from the Aether Flow hero. */
export default function AetherFlowHero({ dark = true }: AetherFlowHeroProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const parent = canvas?.parentElement;
    if (!canvas || !parent) return;

    const context = canvas.getContext('2d');
    if (!context) return;

    let animationFrameId = 0;
    let particles: ParticleState[] = [];
    let width = 1;
    let height = 1;
    const mouse: { x: number | null; y: number | null; radius: number } = {
      x: null,
      y: null,
      radius: 200,
    };

    const initializeParticles = () => {
      particles = [];
      const numberOfParticles = Math.min(190, (height * width) / 9000);
      for (let index = 0; index < numberOfParticles; index += 1) {
        const size = 1.5;
        particles.push({
          x: Math.random() * Math.max(width - size * 4, 1) + size * 2,
          y: Math.random() * Math.max(height - size * 4, 1) + size * 2,
          directionX: Math.random() * 0.4 - 0.2,
          directionY: Math.random() * 0.4 - 0.2,
          size,
        });
      }
    };

    const resizeCanvas = () => {
      const bounds = parent.getBoundingClientRect();
      const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
      width = Math.max(1, bounds.width);
      height = Math.max(1, bounds.height);
      canvas.width = Math.round(width * pixelRatio);
      canvas.height = Math.round(height * pixelRatio);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
      initializeParticles();
    };

    const drawParticle = (particle: ParticleState) => {
      context.beginPath();
      context.arc(particle.x, particle.y, particle.size, 0, Math.PI * 2, false);
      context.fillStyle = dark ? 'rgba(255, 255, 255, 0.82)' : 'rgba(0, 0, 0, 0.72)';
      context.fill();
    };

    const updateParticle = (particle: ParticleState) => {
      if (particle.x > width || particle.x < 0) particle.directionX *= -1;
      if (particle.y > height || particle.y < 0) particle.directionY *= -1;

      if (mouse.x !== null && mouse.y !== null) {
        const deltaX = mouse.x - particle.x;
        const deltaY = mouse.y - particle.y;
        const distance = Math.hypot(deltaX, deltaY);
        if (distance > 0 && distance < mouse.radius + particle.size) {
          const force = (mouse.radius - distance) / mouse.radius;
          particle.x -= (deltaX / distance) * force * 5;
          particle.y -= (deltaY / distance) * force * 5;
        }
      }

      particle.x += particle.directionX;
      particle.y += particle.directionY;
      drawParticle(particle);
    };

    const connectParticles = () => {
      const distanceLimit = (width / 7) * (height / 7);
      for (let first = 0; first < particles.length; first += 1) {
        for (let second = first + 1; second < particles.length; second += 1) {
          const deltaX = particles[first].x - particles[second].x;
          const deltaY = particles[first].y - particles[second].y;
          const distanceSquared = deltaX * deltaX + deltaY * deltaY;
          if (distanceSquared >= distanceLimit) continue;

          const opacity = Math.max(0, 1 - distanceSquared / 20000);
          const mouseDistance = mouse.x === null || mouse.y === null
            ? Number.POSITIVE_INFINITY
            : Math.hypot(particles[first].x - mouse.x, particles[first].y - mouse.y);
          const emphasizedOpacity = mouseDistance < mouse.radius
            ? Math.min(1, opacity * 1.35)
            : opacity;
          context.strokeStyle = dark
            ? `rgba(255, 255, 255, ${emphasizedOpacity})`
            : `rgba(0, 0, 0, ${emphasizedOpacity})`;
          context.lineWidth = 1.5;
          context.beginPath();
          context.moveTo(particles[first].x, particles[first].y);
          context.lineTo(particles[second].x, particles[second].y);
          context.stroke();
        }
      }
    };

    const animate = () => {
      animationFrameId = window.requestAnimationFrame(animate);
      context.fillStyle = dark ? 'black' : '#f7f7f8';
      context.fillRect(0, 0, width, height);
      particles.forEach(updateParticle);
      connectParticles();
    };

    const handleMouseMove = (event: MouseEvent) => {
      const bounds = canvas.getBoundingClientRect();
      mouse.x = event.clientX - bounds.left;
      mouse.y = event.clientY - bounds.top;
    };

    const handleMouseOut = () => {
      mouse.x = null;
      mouse.y = null;
    };

    const resizeObserver = new ResizeObserver(resizeCanvas);
    resizeObserver.observe(parent);
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseout', handleMouseOut);
    resizeCanvas();
    animate();

    return () => {
      resizeObserver.disconnect();
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseout', handleMouseOut);
      window.cancelAnimationFrame(animationFrameId);
    };
  }, [dark]);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      className="pointer-events-none absolute inset-0 h-full w-full"
    />
  );
}

