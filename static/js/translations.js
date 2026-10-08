// translations.js – Safe version (does not override global i18n)
(function() {
    console.log('Translations.js loading (safe mode)');
    
    // Only create TranslationManager if it doesn't exist
    if (typeof TranslationManager === 'undefined') {
        class TranslationManager {
            constructor() {
                // Use existing window.i18n or create safe fallback
                if (window.i18n && window.i18n.translations) {
                    this.translations = window.i18n.translations;
                    this.currentLang = window.i18n.currentLang || 'en';
                } else {
                    this.translations = {};
                    this.currentLang = 'en';
                }
            }
            
            t(key, defaultValue = null) {
                if (this.translations && this.translations[key]) {
                    return this.translations[key];
                }
                return defaultValue || key;
            }
            
            async changeLanguage(lang) {
                window.location.href = `/switch_language/${lang}?next=${encodeURIComponent(window.location.pathname)}`;
            }
        }
        
        window.TranslationManager = TranslationManager;
    }
    
    // Create instance if not already present
    if (!window.i18n || !window.i18n.t) {
        window.i18n = new TranslationManager();
    }
    
    console.log('Translations loaded, current lang:', window.i18n.currentLang);
})();