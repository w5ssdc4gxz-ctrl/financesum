/**
 * Animation Utilities Library
 * Inspired by Retool's smooth animations and interactions
 */

import { gsap } from 'gsap';

// Easing functions matching Retool's style
export const easings = {
  easeInOut: 'cubic-bezier(0.4, 0, 0.2, 1)',
  easeOut: 'cubic-bezier(0, 0, 0.2, 1)',
  easeIn: 'cubic-bezier(0.4, 0, 1, 1)',
  softEaseInOut: 'cubic-bezier(0.45, 0.05, 0.55, 0.95)',
  bounce: 'cubic-bezier(0.68, -0.55, 0.265, 1.55)',
  smooth: 'cubic-bezier(0.25, 0.46, 0.45, 0.94)',
};

// Animation durations (in ms)
export const durations = {
  fast: 200,
  normal: 300,
  slow: 400,
  slower: 600,
  slowest: 800,
};

// Framer Motion variants for common animations
export const fadeInUp = {
  initial: { opacity: 0, y: 30 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -30 },
  transition: { duration: 0.4, ease: [0.25, 0.1, 0.25, 1] }
};

export const fadeIn = {
  initial: { opacity: 0 },
  animate: { opacity: 1 },
  exit: { opacity: 0 },
  transition: { duration: 0.3 }
};

export const scaleIn = {
  initial: { opacity: 0, scale: 0.95 },
  animate: { opacity: 1, scale: 1 },
  exit: { opacity: 0, scale: 0.95 },
  transition: { duration: 0.3, ease: [0.25, 0.1, 0.25, 1] }
};

export const slideInLeft = {
  initial: { opacity: 0, x: -60 },
  animate: { opacity: 1, x: 0 },
  exit: { opacity: 0, x: 60 },
};

export const slideInRight = {
  initial: { opacity: 0, x: 60 },
  animate: { opacity: 1, x: 0 },
  exit: { opacity: 0, x: -60 },
};

export const zoomIn = {
  initial: { opacity: 0, scale: 0.9 },
  animate: { opacity: 1, scale: 1 },
  exit: { opacity: 0, scale: 0.9 },
  transition: { duration: 0.4, ease: [0.34, 1.56, 0.64, 1] }
};

// Stagger children animation
export const staggerContainer = {
  animate: {
    transition: {
      staggerChildren: 0.08,
      delayChildren: 0.1,
    },
  },
};

// GSAP Animation Helpers
export const gsapAnimations = {
  // Fade in with scale
  fadeInScale: (element: HTMLElement, options = {}) => {
    return gsap.fromTo(
      element,
      { opacity: 0, scale: 0.8 },
      {
        opacity: 1,
        scale: 1,
        duration: 0.6,
        ease: 'power2.out',
        ...options,
      }
    );
  },

  // Slide in from bottom
  slideInBottom: (element: HTMLElement, options = {}) => {
    return gsap.fromTo(
      element,
      { opacity: 0, y: 100 },
      {
        opacity: 1,
        y: 0,
        duration: 0.8,
        ease: 'power3.out',
        ...options,
      }
    );
  },

  // Parallax effect
  parallax: (element: HTMLElement, speed = 0.5) => {
    return gsap.to(element, {
      y: () => window.scrollY * speed,
      ease: 'none',
      scrollTrigger: {
        trigger: element,
        start: 'top bottom',
        end: 'bottom top',
        scrub: true,
      },
    });
  },

  // Number counter animation
  countUp: (element: HTMLElement, endValue: number, options = {}) => {
    const obj = { value: 0 };
    return gsap.to(obj, {
      value: endValue,
      duration: 2,
      ease: 'power1.out',
      onUpdate: () => {
        element.textContent = Math.round(obj.value).toString();
      },
      ...options,
    });
  },

  // Gradient animation
  gradientShift: (element: HTMLElement) => {
    return gsap.to(element, {
      backgroundPosition: '200% center',
      duration: 3,
      ease: 'none',
      repeat: -1,
    });
  },
};

// D3 Animation Helpers
export const d3Animations = {
  // Smooth transition for D3 selections
  transition: (duration = 600) => {
    return {
      duration,
      ease: 'cubicInOut',
    };
  },

  // Stagger delay for multiple elements
  staggerDelay: (index: number, baseDelay = 50) => {
    return index * baseDelay;
  },
};

// Scroll-triggered animation utilities
export class ScrollAnimationObserver {
  private observer: IntersectionObserver;
  private elements: Set<Element>;

  constructor(
    callback: (entry: IntersectionObserverEntry) => void,
    options = {}
  ) {
    this.elements = new Set();
    this.observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            callback(entry);
          }
        });
      },
      {
        threshold: 0.1,
        rootMargin: '0px 0px -100px 0px',
        ...options,
      }
    );
  }

  observe(element: Element) {
    this.elements.add(element);
    this.observer.observe(element);
  }

  unobserve(element: Element) {
    this.elements.delete(element);
    this.observer.unobserve(element);
  }

  disconnect() {
    this.observer.disconnect();
    this.elements.clear();
  }
}

// Utility to create scroll-triggered animations
export const createScrollAnimation = (
  selector: string,
  animationClass: string
) => {
  if (typeof window === 'undefined') return;

  const observer = new ScrollAnimationObserver((entry) => {
    entry.target.classList.add(animationClass);
  });

  document.querySelectorAll(selector).forEach((el) => {
    observer.observe(el);
  });

  return observer;
};

// Hover effects
export const hoverEffects = {
  scale: {
    whileHover: { scale: 1.05 },
    whileTap: { scale: 0.95 },
    transition: { type: 'spring', stiffness: 400, damping: 17 },
  },
  lift: {
    whileHover: { y: -4, boxShadow: '0 20px 25px -5px rgba(0, 0, 0, 0.1)' },
    transition: { duration: 0.2 },
  },
  glow: {
    whileHover: {
      boxShadow: '0 0 20px rgba(168, 85, 247, 0.4)',
    },
    transition: { duration: 0.3 },
  },
};

// Loading animations
export const loadingVariants = {
  pulse: {
    scale: [1, 1.05, 1],
    opacity: [0.5, 1, 0.5],
    transition: {
      duration: 2,
      repeat: Infinity,
      ease: 'easeInOut',
    },
  },
  shimmer: {
    backgroundPosition: ['200% 0', '-200% 0'],
    transition: {
      duration: 2,
      repeat: Infinity,
      ease: 'linear',
    },
  },
};

// Page transition variants
export const pageTransition = {
  initial: { opacity: 0, y: 20 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -20 },
  transition: { duration: 0.3, ease: easings.easeInOut },
};
