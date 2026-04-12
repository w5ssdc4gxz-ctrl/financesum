import { motion, useMotionValue, useTransform, PanInfo } from 'framer-motion';
import { useState, useEffect } from 'react';

type CardRotateProps = {
    children: React.ReactNode;
    onSendToBack: () => void;
    sensitivity: number;
};

function CardRotate({ children, onSendToBack, sensitivity }: CardRotateProps) {
    const x = useMotionValue(0);
    const y = useMotionValue(0);
    const rotateX = useTransform(y, [-100, 100], [60, -60]);
    const rotateY = useTransform(x, [-100, 100], [-60, 60]);

    function handleDragEnd(_: any, info: PanInfo) {
        if (Math.abs(info.offset.x) > sensitivity || Math.abs(info.offset.y) > sensitivity) {
            onSendToBack();
        } else {
            x.set(0);
            y.set(0);
        }
    }

    return (
        <motion.div
            className="absolute cursor-grab active:cursor-grabbing"
            style={{ x, y, rotateX, rotateY }}
            drag
            dragConstraints={{ top: 0, right: 0, bottom: 0, left: 0 }}
            dragElastic={0.6}
            whileTap={{ cursor: 'grabbing' }}
            onDragEnd={handleDragEnd}
        >
            {children}
        </motion.div>
    );
}

type StackProps = {
    randomRotation?: boolean;
    sensitivity?: number;
    cardDimensions?: { width: number; height: number };
    cardsData?: { id: number; img: string }[];
    animationConfig?: { stiffness: number; damping: number };
    sendToBackOnClick?: boolean;
};

export default function Stack({
    randomRotation = false,
    sensitivity = 200,
    cardDimensions = { width: 208, height: 208 },
    cardsData = [],
    animationConfig = { stiffness: 260, damping: 20 },
    sendToBackOnClick = false
}: StackProps) {
    const [cards, setCards] = useState(
        cardsData.length
            ? cardsData
            : [
                { id: 1, img: '/concept-1.png' },
                { id: 2, img: '/concept-2.png' },
                { id: 3, img: '/concept-3.png' },
            ]
    );

    // Store random rotations in state to ensure consistency between renders
    const [rotations, setRotations] = useState<Record<number, number>>({});

    useEffect(() => {
        if (randomRotation) {
            const newRotations: Record<number, number> = {};
            cards.forEach(card => {
                newRotations[card.id] = Math.random() * 10 - 5;
            });
            setRotations(newRotations);
        }
    }, [cards, randomRotation]);

    const sendToBack = (id: number) => {
        setCards(prev => {
            const newCards = [...prev];
            const index = newCards.findIndex(card => card.id === id);
            const [card] = newCards.splice(index, 1);
            newCards.unshift(card);
            return newCards;
        });
    };

    return (
        <div
            className="relative"
            style={{
                width: cardDimensions.width,
                height: cardDimensions.height,
                perspective: 600
            }}
        >
            {cards.map((card, index) => {
                const randomRotate = rotations[card.id] || 0;

                return (
                    <CardRotate key={card.id} onSendToBack={() => sendToBack(card.id)} sensitivity={sensitivity}>
                        <motion.div
                            className="overflow-hidden rounded-2xl shadow-2xl"
                            onClick={() => sendToBackOnClick && sendToBack(card.id)}
                            animate={{
                                rotateZ: (cards.length - index - 1) * 4 + randomRotate,
                                scale: 1 + index * 0.06 - cards.length * 0.06,
                                transformOrigin: '90% 90%'
                            }}
                            initial={false}
                            transition={{
                                type: 'spring',
                                stiffness: animationConfig.stiffness,
                                damping: animationConfig.damping
                            }}
                            style={{
                                width: cardDimensions.width,
                                height: cardDimensions.height
                            }}
                        >
                            <img src={card.img} alt={`card-${card.id}`} className="pointer-events-none h-full w-full object-cover" />
                        </motion.div>
                    </CardRotate>
                );
            })}
        </div>
    );
}
