// Correctness only: what the browser would drop or misread. No rules about
// how the stylesheets are laid out. A stylesheet that does not parse (a lost
// "*/", an unclosed brace) always fails.
export default {
  rules: {
    'annotation-no-unknown': true,
    'at-rule-no-unknown': true,
    'color-no-invalid-hex': true,
    'declaration-block-no-duplicate-properties': [true, { ignore: ['consecutive-duplicates-with-different-values'] }],
    'declaration-property-value-no-unknown': true,
    'function-calc-no-unspaced-operator': true,
    'keyframe-declaration-no-important': true,
    'media-feature-name-no-unknown': true,
    'no-invalid-double-slash-comments': true,
    'no-invalid-position-at-import-rule': true,
    'property-no-unknown': true,
    'selector-pseudo-class-no-unknown': true,
    'selector-pseudo-element-no-unknown': true,
    'selector-type-no-unknown': [true, { ignore: ['custom-elements'] }],
    'string-no-newline': true,
    'unit-no-unknown': true,
  },
};
