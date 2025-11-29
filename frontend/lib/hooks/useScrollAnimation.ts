import { useEffect, useRef, RefObject, useState } from 'react';
import { gsap } from 'gsap';

interface UseScrollAnimationOptions {
  threshold?: number;
  rootMargin?: string;
  triggerOnce?: boolean;
  animationClass?: string;
  delay?: number;
}

export function useScrollAnimation<T extends HTMLElement>(
  options: UseScrollAnimationOptions = {}
): RefObject<T> {
  const elementRef = useRef<T>(null);
  const {
    threshold = 0.1,
    rootMargin = '50px',
    triggerOnce = true,
    animationClass = 'animate-fade-in-up',
    delay = 0,
  } = options;

  useEffect(() => {
    const element = elementRef.current;
    if (!element) return;

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            setTimeout(() => {
              element.classList.add(animationClass);
            }, delay);

            if (triggerOnce) {
              observer.unobserve(element);
            }
          } else if (!triggerOnce) {
            element.classList.remove(animationClass);
          }
        });
      },
      { threshold, rootMargin }
    );

    observer.observe(element);

    return () => {
      observer.disconnect();
    };
  }, [threshold, rootMargin, triggerOnce, animationClass, delay]);

  return elementRef;
}

// Hook for GSAP scroll animations
export function useGSAPScroll<T extends HTMLElement>(
  animation: (element: HTMLElement) => void,
  deps: any[] = []
): RefObject<T> {
  const elementRef = useRef<T>(null);

  useEffect(() => {
    const element = elementRef.current;
    if (!element) return;

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            animation(element);
            observer.unobserve(element);
          }
        });
      },
      { threshold: 0.1, rootMargin: '50px' }
    );

    observer.observe(element);

    return () => {
      observer.disconnect();
    };
  }, deps);

  return elementRef;
}

// Hook for parallax effects
export function useParallax<T extends HTMLElement>(speed: number = 0.5): RefObject<T> {
  const elementRef = useRef<T>(null);

  useEffect(() => {
    const element = elementRef.current;
    if (!element) return;

    const handleScroll = () => {
      const rect = element.getBoundingClientRect();
      const scrolled = window.scrollY;
      const offsetTop = rect.top + scrolled;
      const parallax = (scrolled - offsetTop) * speed;

      gsap.to(element, {
        y: parallax,
        duration: 0.5,
        ease: 'power1.out',
      });
    };

    window.addEventListener('scroll', handleScroll, { passive: true });
    handleScroll();

    return () => {
      window.removeEventListener('scroll', handleScroll);
    };
  }, [speed]);

  return elementRef;
}

// Hook for stagger animations
export function useStagger<T extends HTMLElement>(
  childSelector: string = '*',
  delay: number = 0.1
): RefObject<T> {
  const containerRef = useRef<T>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const children = container.querySelectorAll(childSelector);

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            gsap.from(children, {
              opacity: 0,
              y: 30,
              duration: 0.6,
              stagger: delay,
              ease: 'power2.out',
            });
            observer.unobserve(container);
          }
        });
      },
      { threshold: 0.1 }
    );

    observer.observe(container);

    return () => {
      observer.disconnect();
    };
  }, [childSelector, delay]);

  return containerRef;
}

// Hook for hover effects
export function useHover<T extends HTMLElement>(
  onHover?: () => void,
  onLeave?: () => void
): RefObject<T> {
  const elementRef = useRef<T>(null);

  useEffect(() => {
    const element = elementRef.current;
    if (!element) return;

    const handleMouseEnter = () => {
      gsap.to(element, {
        scale: 1.05,
        duration: 0.3,
        ease: 'power2.out',
      });
      onHover?.();
    };

    const handleMouseLeave = () => {
      gsap.to(element, {
        scale: 1,
        duration: 0.3,
        ease: 'power2.out',
      });
      onLeave?.();
    };

    element.addEventListener('mouseenter', handleMouseEnter);
    element.addEventListener('mouseleave', handleMouseLeave);

    return () => {
      element.removeEventListener('mouseenter', handleMouseEnter);
      element.removeEventListener('mouseleave', handleMouseLeave);
    };
  }, [onHover, onLeave]);

  return elementRef;
}

// Hook for scroll progress
export function useScrollProgress(): number {
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    const calculateProgress = () => {
      const windowHeight = window.innerHeight;
      const documentHeight = document.documentElement.scrollHeight;
      const scrollTop = window.scrollY;
      const maxScroll = documentHeight - windowHeight;
      const progress = (scrollTop / maxScroll) * 100;
      setProgress(Math.min(progress, 100));
    };

    window.addEventListener('scroll', calculateProgress, { passive: true });
    calculateProgress();

    return () => {
      window.removeEventListener('scroll', calculateProgress);
    };
  }, []);

  return progress;
}

// Hook for element in view
export function useInView<T extends HTMLElement>(
  threshold: number = 0.1
): [RefObject<T>, boolean] {
  const elementRef = useRef<T>(null);
  const [isInView, setIsInView] = useState(false);

  useEffect(() => {
    const element = elementRef.current;
    if (!element) return;

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          setIsInView(entry.isIntersecting);
        });
      },
      { threshold }
    );

    observer.observe(element);

    return () => {
      observer.disconnect();
    };
  }, [threshold]);

  return [elementRef, isInView];
}

