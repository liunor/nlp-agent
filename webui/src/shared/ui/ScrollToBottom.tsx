import React from 'react';

interface ScrollToBottomProps {
  isLoading: boolean;
  isVisible: boolean;
  onClick: () => void;
}

export const ScrollToBottom: React.FC<ScrollToBottomProps> = ({ isLoading, isVisible, onClick }) => {
  return (
    <button
      onClick={onClick}
      className={`absolute bottom-[208px] left-1/2 -translate-x-1/2 z-50 flex items-center justify-center w-10 h-10 
      bg-white hover:bg-gray-50 border border-gray-200 rounded-full shadow-md
      transition-all duration-300 ease-[cubic-bezier(0.34,1.56,0.64,1)]
      ${isVisible 
        ? 'opacity-100 translate-y-0 scale-100 pointer-events-auto' 
        : 'opacity-0 translate-y-4 scale-50 pointer-events-none'
      }`}
    >
      {isLoading ? (
        <svg className="w-5 h-5 text-blue-500 animate-spin" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
      ) : (
        <svg className="w-5 h-5 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 14l-7 7m0 0l-7-7m7 7V3"></path>
        </svg>
      )}
    </button>
  );
};