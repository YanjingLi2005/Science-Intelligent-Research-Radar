import { useEffect, useRef } from 'react';

interface ConstellationNode {
  x: number;
  y: number;
  vx: number;
  vy: number;
  baseX: number;
  baseY: number;
  radius: number;
  pulse: number;
}

interface ConstellationGridProps {
  dark?: boolean;
}

export default function ConstellationGrid({ dark = false }: ConstellationGridProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const parent = canvas?.parentElement;
    if (!canvas || !parent) return;

    const context = canvas.getContext('2d');
    if (!context) return;

    let frameId = 0;
    let width = 0;
    let height = 0;
    let nodes: ConstellationNode[] = [];
    let lastTime = performance.now();
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    const pointer = {
      x: -1000,
      y: -1000,
      previousX: -1000,
      previousY: -1000,
      velocityX: 0,
      velocityY: 0,
      radius: 190,
    };

    const createNodes = () => {
      nodes = [];
      const spacing = width < 640 ? 72 : 62;
      const columns = Math.ceil(width / spacing) + 1;
      const rows = Math.ceil(height / spacing) + 1;

      for (let column = 0; column < columns; column += 1) {
        for (let row = 0; row < rows; row += 1) {
          const x = column * spacing;
          const y = row * spacing;
          nodes.push({
            x,
            y,
            vx: 0,
            vy: 0,
            baseX: x,
            baseY: y,
            radius: Math.random() * 0.8 + 0.8,
            pulse: Math.random() * Math.PI * 2,
          });
        }
      }
    };

    const resize = () => {
      const bounds = parent.getBoundingClientRect();
      const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
      width = Math.max(1, bounds.width);
      height = Math.max(1, bounds.height);
      canvas.width = Math.round(width * pixelRatio);
      canvas.height = Math.round(height * pixelRatio);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
      createNodes();
    };

    const movePointer = (event: PointerEvent) => {
      const bounds = canvas.getBoundingClientRect();
      pointer.x = event.clientX - bounds.left;
      pointer.y = event.clientY - bounds.top;
    };

    const clearPointer = () => {
      pointer.x = -1000;
      pointer.y = -1000;
      pointer.previousX = -1000;
      pointer.previousY = -1000;
    };

    const draw = (now: number) => {
      const delta = Math.min((now - lastTime) / 1000, 0.05);
      lastTime = now;
      pointer.velocityX = (pointer.x - pointer.previousX) / Math.max(delta * 1000, 1);
      pointer.velocityY = (pointer.y - pointer.previousY) / Math.max(delta * 1000, 1);
      pointer.previousX = pointer.x;
      pointer.previousY = pointer.y;

      const speed = Math.hypot(pointer.velocityX, pointer.velocityY);
      const background = dark ? '#0d0d0f' : '#f7f7f8';
      const foreground = dark ? '255, 255, 255' : '18, 18, 20';

      context.fillStyle = background;
      context.fillRect(0, 0, width, height);

      for (const node of nodes) {
        node.pulse += delta * 1.7;
        const deltaX = pointer.x - node.x;
        const deltaY = pointer.y - node.y;
        const distance = Math.hypot(deltaX, deltaY);

        if (!reduceMotion && distance < pointer.radius && distance > 0) {
          const strength = 1 - distance / pointer.radius;
          const force = strength * (700 + Math.min(speed, 3) * 110);
          const angle = Math.atan2(deltaY, deltaX);
          node.vx -= Math.cos(angle) * force * delta;
          node.vy -= Math.sin(angle) * force * delta;
        }

        node.vx += (node.baseX - node.x) * 16 * delta;
        node.vy += (node.baseY - node.y) * 16 * delta;
        node.vx *= 0.82;
        node.vy *= 0.82;
        node.x += node.vx * delta * 60;
        node.y += node.vy * delta * 60;
      }

      const connectionDistance = 82;
      const connectionDistanceSquared = connectionDistance ** 2;
      for (let index = 0; index < nodes.length; index += 1) {
        const node = nodes[index];
        for (let nextIndex = index + 1; nextIndex < nodes.length; nextIndex += 1) {
          const other = nodes[nextIndex];
          const deltaX = node.x - other.x;
          const deltaY = node.y - other.y;
          const distanceSquared = deltaX ** 2 + deltaY ** 2;
          if (distanceSquared >= connectionDistanceSquared) continue;

          const alpha = (1 - Math.sqrt(distanceSquared) / connectionDistance) * (dark ? 0.12 : 0.09);
          context.strokeStyle = `rgba(${foreground}, ${alpha})`;
          context.lineWidth = 0.65;
          context.beginPath();
          context.moveTo(node.x, node.y);
          context.lineTo(other.x, other.y);
          context.stroke();
        }
      }

      for (const node of nodes) {
        const distance = Math.hypot(pointer.x - node.x, pointer.y - node.y);
        const nearby = !reduceMotion && distance < pointer.radius;
        const alpha = nearby ? 0.72 : 0.18 + Math.sin(node.pulse) * 0.05;
        const radius = nearby ? node.radius * 1.8 : node.radius;

        context.fillStyle = `rgba(${foreground}, ${alpha})`;
        context.beginPath();
        context.arc(node.x, node.y, radius, 0, Math.PI * 2);
        context.fill();

        if (nearby && distance < 78) {
          context.strokeStyle = `rgba(${foreground}, ${dark ? 0.2 : 0.13})`;
          context.lineWidth = 0.8;
          context.beginPath();
          context.arc(node.x, node.y, 5 + ((node.pulse * 7) % 16), 0, Math.PI * 2);
          context.stroke();
        }
      }

      frameId = window.requestAnimationFrame(draw);
    };

    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(parent);
    window.addEventListener('pointermove', movePointer, { passive: true });
    document.addEventListener('mouseleave', clearPointer);
    resize();
    frameId = window.requestAnimationFrame(draw);

    return () => {
      window.cancelAnimationFrame(frameId);
      resizeObserver.disconnect();
      window.removeEventListener('pointermove', movePointer);
      document.removeEventListener('mouseleave', clearPointer);
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
